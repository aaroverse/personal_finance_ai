# 웹 인증과 수정 이력 및 동시 검토 충돌을 합성 데이터로 검증합니다.
import json
import sqlite3
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import httpx
import pytest

from finance_bot.database import FinanceRepository, RecordConflictError
from finance_bot.web import LedgerReader, make_handler
from finance_bot.web_auth import LoginError, WebAuth
from finance_bot.bot import _review_keyboard
from test_web import add


@contextmanager
def running(tmp_path):
    repo = FinanceRepository(tmp_path / 'ledger.db')
    repo.initialize()
    repo.prepare_web_edits()
    reader = LedgerReader(tmp_path / 'ledger.db', 42)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(reader))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
        client.headers['Origin'] = str(client.base_url).rstrip('/')
        try:
            yield repo, reader, client
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def login(client):
    result = client.post('/api/setup', json={'password': 'synthetic-test-password'})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['set-cookie']
    assert 'SameSite=Strict' in result.headers['set-cookie']
    client.headers['X-CSRF-Token'] = result.json()['csrf']


def test_auth_setup_hash_cookie_logout_and_throttling(tmp_path):
    auth = WebAuth(tmp_path / 'auth.json')
    with pytest.raises(LoginError):
        auth.login('short', setup=True)
    token, csrf = auth.login('synthetic-test-password', setup=True)
    assert auth.session(token) == csrf
    assert 'synthetic-test-password' not in auth.path.read_text()
    assert auth.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(LoginError):
        auth.login('overwrite-password', setup=True)
    auth.logout(token)
    assert auth.session(token) is None
    assert auth.session('invalid') is None
    restarted = WebAuth(auth.path)
    new_token, new_csrf = restarted.login('synthetic-test-password')
    assert restarted.session(new_token) == new_csrf
    restarted._sessions[new_token] = (new_csrf, 0)
    assert restarted.session(new_token) is None
    for _ in range(5):
        with pytest.raises(LoginError):
            auth.login('wrong-password')
    with pytest.raises(LoginError, match='一分钟'):
        auth.login('synthetic-test-password')


def test_backup_contains_original_data_and_is_created_once(tmp_path):
    repo = FinanceRepository(tmp_path / 'ledger.db')
    repo.initialize()
    add(repo)
    repo.prepare_web_edits()
    repo.prepare_web_edits()
    backups = list((tmp_path / 'backups').glob('*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 1
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ledger_edits'").fetchone() is None


def test_api_enforces_auth_origin_csrf_and_logs_out(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        add(repo)
        assert client.get('/api/ledger').status_code == 401
        assert client.get('/api/session').json()['needs_setup'] is True
        assert client.post('/api/setup', json={'password': 'synthetic-test-password'}, headers={'Origin': 'https://evil.example'}).status_code == 403
        login(client)
        assert client.cookies.get('finance_session')
        assert client.get('/api/session').json()['authenticated'] is True
        row = reader.read({'month':['']})['records'][0]
        url = f"/api/transactions/{row['id']}/edit"
        assert client.post(url, json={'revision':row['revision']}, headers={'X-CSRF-Token':'wrong'}).status_code == 403
        assert client.post('/api/logout', json={}).status_code == 200
        assert client.get('/api/ledger').status_code == 401


def test_edit_validates_preserves_source_and_rejects_stale_edits(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        add(repo)
        login(client)
        row = reader.read({'month':['']})['records'][0]
        expense = {key: row[key] for key in ['transaction_date','amount','currency','merchant','category','payment_account','note']}
        url = f"/api/transactions/{row['id']}/edit"
        expense['amount'] = '-1'
        assert client.post(url, json={'revision':row['revision'],'expense':expense}).status_code == 422
        expense.update(amount='15.30', category='shopping', payment_account='SG DBS 信用卡')
        response = client.post(url, json={'revision':row['revision'],'expense':expense})
        assert response.status_code == 200
        current = reader.read({'month':['']})['records'][0]
        assert current['amount'] == '15.30'
        assert current['source_text'] == row['source_text']
        assert current['revision'] != row['revision']
        assert client.post(url, json={'revision':row['revision'],'expense':expense}).status_code == 409
        with sqlite3.connect(reader.path) as db:
            edits = db.execute('SELECT previous_json, updated_json FROM ledger_edits').fetchall()
        assert len(edits) == 1
        assert json.loads(edits[0][0])['amount'] == '0.10'
        assert json.loads(edits[0][1])['amount'] == '15.30'


def test_draft_review_is_atomic_and_owner_scoped(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        draft = add(repo, pending=True)
        other = add(repo, owner=84, pending=True)
        login(client)
        row = reader.read({'month':[''], 'state':['pending']})['records'][0]
        body = {'revision':row['revision']}
        assert client.post(f'/api/drafts/{other.id}/confirm', json=body).status_code == 409
        assert client.post(f'/api/drafts/{draft.id}/confirm', json=body).status_code == 200
        assert client.post(f'/api/drafts/{draft.id}/confirm', json=body).status_code == 409
        assert client.post(f'/api/drafts/{draft.id}/cancel', json=body).status_code == 409
        assert repo.count_transactions() == 1


def test_telegram_revision_blocks_stale_web_review(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        draft = add(repo, pending=True)
        login(client)
        row = reader.read({'month':[''], 'state':['pending']})['records'][0]
        updated = draft.expense.model_copy(update={'merchant':'Telegram 新描述'})
        repo.update_draft(draft.id, 42, '新原始描述', updated)
        assert client.post(f'/api/drafts/{draft.id}/confirm', json={'revision':row['revision']}).status_code == 409
        assert repo.count_transactions() == 0


def test_web_edit_blocks_stale_telegram_buttons_and_pending_ai_write(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        draft = add(repo, pending=True)
        keyboard = _review_keyboard(draft)
        callback = keyboard.inline_keyboard[0][0].callback_data
        assert len(callback.encode()) <= 64
        revision = callback.split(':')[2]
        row = reader.read({'month':[''], 'state':['pending']})['records'][0]
        edited = draft.expense.model_copy(update={'merchant':'网页新商户'})
        repo.edit_record('drafts', draft.id, 42, edited, row['revision'])
        with pytest.raises(RecordConflictError):
            repo.confirm_draft(draft.id, 42, revision)
        with pytest.raises(RecordConflictError):
            repo.update_draft(draft.id, 42, '旧的 AI 修改', draft.expense, draft.revision)
        assert repo.count_transactions() == 0


def test_pending_edit_then_cancel_and_wrong_owner_transaction_edit(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        draft = add(repo, pending=True)
        add(repo, owner=84)
        login(client)
        row = reader.read({'month':[''], 'state':['pending']})['records'][0]
        expense = {key:row[key] for key in ['transaction_date','amount','currency','merchant','category','payment_account','note']}
        expense['amount'] = '9.50'
        saved = client.post(f'/api/drafts/{draft.id}/edit', json={'revision':row['revision'],'expense':expense})
        assert saved.status_code == 200
        assert reader.read({'month':['']})['confirmed_count'] == 0
        assert client.post('/api/transactions/1/edit', json={'revision':row['revision'],'expense':expense}).status_code == 409
        assert client.post(f'/api/drafts/{draft.id}/cancel', json={'revision':saved.json()['revision']}).status_code == 200
        assert reader.read({'month':[''], 'state':['pending']})['total'] == 0


def test_simultaneous_confirmations_create_only_one_transaction(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from finance_bot.database import DraftNotPendingError
    repo = FinanceRepository(tmp_path / 'ledger.db')
    repo.initialize()
    draft = add(repo, pending=True)
    def confirm():
        try:
            repo.confirm_draft(draft.id, 42, draft.revision)
            return True
        except DraftNotPendingError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: confirm(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert repo.count_transactions() == 1
