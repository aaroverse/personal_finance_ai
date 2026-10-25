# 명세서 원본 사실과 사용자 검토 초안을 로컬에 저장하고 소유자별로 복원합니다.
import copy
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import get_args

from .database import FinanceRepository, RecordConflictError
from .models import ExpenseCategory


def now():
    return datetime.now(UTC).isoformat(timespec='microseconds')


def prepare_reviews(repo: FinanceRepository):
    with closing(repo._connect()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_reviews'").fetchone():
            return
        folder = repo._path.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (repo._path.stem + '-before-review-progress-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f') + '.db')
        with closing(sqlite3.connect(path)) as backup:
            conn.backup(backup)
        path.chmod(0o600)
        conn.execute('''CREATE TABLE statement_reviews (
            id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL, preview_json TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(telegram_user_id,source_sha256)
        )''')
        conn.commit()


def review_by_source(repo, owner, digest):
    with closing(repo._connect()) as conn:
        row = conn.execute('SELECT id FROM statement_reviews WHERE telegram_user_id=? AND source_sha256=?', (owner, digest)).fetchone()
        return row['id'] if row else None


def create_review(repo, owner, preview):
    raw = copy.deepcopy(preview)
    for key in ('preview_id','review_id','progress_version','review_state','preprocessing'):
        raw.pop(key, None)
    for row in raw['rows']:
        for key in ('candidates','candidate_count','possible_duplicate_count','processed','suggestion','review_group','intra_statement_duplicate','correction_history','correction_count','latest_correction_id','requires_recheck'):
            row.pop(key, None)
    for card in raw['cards']:
        card.pop('personal_default', None)
        card.pop('personal_default_saved', None)
    with closing(repo._connect()) as conn, conn:
        stamp = now()
        conn.execute('''INSERT OR IGNORE INTO statement_reviews
            (id,telegram_user_id,source_sha256,preview_json,created_at,updated_at) VALUES(?,?,?,?,?,?)''',
            (secrets.token_urlsafe(24), owner, raw['source_sha256'], json.dumps(raw,ensure_ascii=False), stamp, stamp))
        return conn.execute('SELECT id FROM statement_reviews WHERE telegram_user_id=? AND source_sha256=?',
            (owner, raw['source_sha256'])).fetchone()['id']


def get_review(conn, owner, review_id):
    if not isinstance(review_id, str) or len(review_id) > 64:
        raise ValueError('请选择已保存的账单。')
    record = conn.execute('SELECT * FROM statement_reviews WHERE id=? AND telegram_user_id=?', (review_id, owner)).fetchone()
    if not record:
        raise RecordConflictError('找不到这份账单，请从账单列表重新打开。')
    return record


def check_version(conn, owner, preview):
    if 'review_id' not in preview:
        return
    record = get_review(conn, owner, preview['review_id'])
    if record['version'] != preview['progress_version']:
        raise RecordConflictError('这份账单已在其他页面更新。请从账单列表重新打开，避免覆盖较新的进度。')


def touch_review(conn, owner, preview):
    if 'review_id' in preview:
        conn.execute('UPDATE statement_reviews SET version=version+1,updated_at=? WHERE id=? AND telegram_user_id=?',
            (now(), preview['review_id'], owner))


def candidate_context(row):
    context = {'candidates':[[c['id'],c['revision']] for c in row.get('candidates',[])],
        'count':row.get('candidate_count',0), 'duplicates':row.get('possible_duplicate_count',0),
        'intra':bool(row.get('intra_statement_duplicate'))}
    if row.get('latest_correction_id') is not None:
        context['correction_id']=row['latest_correction_id']
    return context


def open_review(repo, owner, review_id):
    from .statements import suggest_matches
    with closing(repo._connect()) as conn:
        # Pin metadata/state to one read transaction. Fresh candidates still get revision checks on save.
        conn.execute('BEGIN')
        record = dict(get_review(conn, owner, review_id))
    preview = suggest_matches(json.loads(record['preview_json']), repo._path, owner)
    state = json.loads(record['state_json'])
    state.setdefault('choices', {})
    for row in preview['rows']:
        choice = state['choices'].get(row['row_key'])
        if choice and row['kind']=='charge' and not row['processed'] and choice.get('context') != candidate_context(row):
            choice.update(action='', reviewed=False, acknowledge=False, rechecked=False)
            row['suggestion']['reasons'].append('记账记录有变化，请重新核对')
    preview.update(review_id=review_id, progress_version=record['version'], review_state=state)
    return preview


def validate_state(preview, body):
    choices = body.get('choices')
    phase = body.get('phase')
    if phase not in {'ownership','review'} or not isinstance(choices,dict) or len(choices)>1000:
        raise ValueError('审核进度格式无效。')
    rows = {r['row_key']:r for r in preview['rows']}
    valid_categories = set(get_args(ExpenseCategory)) - {'transfer','cash_withdrawal'}
    cleaned = {}
    flags = ('is_personal','reviewed','deferred','acknowledge','dirty')
    for key, choice in choices.items():
        if key not in rows or not isinstance(choice,dict) or set(choice)-set(flags)-{'action','category','rechecked'}:
            raise ValueError('审核进度包含未知明细或字段。')
        if any(type(choice.get(flag)) is not bool for flag in flags):
            raise ValueError('请明确每笔明细的审核状态。')
        if type(choice.get('rechecked',False)) is not bool:
            raise ValueError('重新核对状态无效。')
        action, category = choice.get('action'), choice.get('category')
        if not isinstance(action,str) or (action not in {'','import'} and action not in {str(c['id']) for c in rows[key].get('candidates',[])}):
            raise ValueError('关联记录不在当前候选中，请重新核对。')
        if not isinstance(category,str) or (category and category not in valid_categories):
            raise ValueError('请选择有效分类。')
        cleaned[key] = {**choice, 'context':candidate_context(rows[key])}
    cards = {c['account'] for c in preview['cards']}
    applied = body.get('applied_cards', [])
    drafts = body.get('card_drafts', {})
    if not isinstance(applied,list) or len(applied)>len(cards) or any(not isinstance(c,str) or c not in cards for c in applied):
        raise ValueError('卡片归属进度无效。')
    if not isinstance(drafts,dict) or set(drafts)-cards:
        raise ValueError('卡片草稿无效。')
    for draft in drafts.values():
        if not isinstance(draft,dict) or set(draft)!={'personal','remember'} or any(type(v) is not bool for v in draft.values()):
            raise ValueError('卡片草稿无效。')
    return {'phase':phase,'choices':cleaned,'applied_cards':list(set(applied)),'card_drafts':drafts}


def save_progress(repo, owner, preview, body):
    state = validate_state(preview, body)
    with closing(repo._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        check_version(conn, owner, preview)
        conn.execute('UPDATE statement_reviews SET state_json=?,version=version+1,updated_at=? WHERE id=? AND telegram_user_id=?',
            (json.dumps(state,ensure_ascii=False), now(), preview['review_id'], owner))
    return preview['progress_version'] + 1


def list_reviews(repo, owner, page=1):
    if type(page) is not int or page<1:
        raise ValueError('页码无效。')
    with closing(repo._connect()) as conn:
        conn.execute('BEGIN')
        total = conn.execute('SELECT count(*) FROM statement_reviews WHERE telegram_user_id=?', (owner,)).fetchone()[0]
        pages = max(1,(total+24)//25)
        page = min(page,pages)
        records = conn.execute('''SELECT * FROM statement_reviews WHERE telegram_user_id=?
            ORDER BY updated_at DESC,id LIMIT 25 OFFSET ?''', (owner,(page-1)*25)).fetchall()
        linked = {r['row_key'] for r in conn.execute('SELECT row_key FROM statement_links WHERE telegram_user_id=?',(owner,))}
    items = []
    for record in records:
        data, state = json.loads(record['preview_json']), json.loads(record['state_json'])
        charges = [r for r in data['rows'] if r['kind']=='charge']
        confirmed = sum(r['row_key'] in linked for r in charges)
        deferred = sum(r['row_key'] not in linked and state.get('choices',{}).get(r['row_key'],{}).get('deferred',False) for r in charges)
        items.append({'id':record['id'],'bank':data['bank'],'statement_date':data['statement_date'],
            'card_count':len(data['cards']),'charge_count':len(charges),'confirmed_count':confirmed,
            'deferred_count':deferred,'updated_at':record['updated_at'],
            'status':'complete' if confirmed==len(charges) else 'partial' if confirmed else 'pending'})
    return {'items':items,'page':page,'pages':pages,'total':total}
