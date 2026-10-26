# 복구 가능한 거래 삭제와 UOB 일괄 철회의 범위 및 원자성을 검증합니다.
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import httpx
import pytest

from finance_bot.database import FinanceRepository,RecordConflictError
from finance_bot.transaction_deletions import (prepare_deletions,change_deletion,
    uob_import_plan,withdraw_uob_imports,backup_ledger)
from finance_bot.reconciliation import confirm_rows
from finance_bot.statement_reviews import create_review,open_review
from finance_bot.web import LedgerReader,make_handler,WebSettings
from test_statement_reviews import setup
from test_web import add
from test_web_editing import running,login


def manual(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db');repo.initialize();repo.prepare_web_edits();prepare_deletions(repo)
    add(repo,amount='7.50');reader=LedgerReader(repo._path,42)
    return repo,reader,reader.read({'month':['']})['records'][0]


def test_delete_restore_keep_record_and_source_adjust_totals_and_recent(tmp_path):
    repo,reader,row=manual(tmp_path)
    change_deletion(repo,42,row['id'],row['revision'],'delete')
    assert reader.read({'month':['']})['totals']['SGD']=='0.00'
    assert repo.recent_transactions(42)==[] and repo.count_transactions()==0
    deleted=reader.read({'state':['deleted'],'month':['']})['records'][0]
    assert deleted['source_text']==row['source_text'] and deleted['amount']=='7.50'
    assert deleted['revision']!=row['revision']
    change_deletion(repo,42,row['id'],deleted['revision'],'restore')
    restored=reader.read({'month':['']})['records'][0]
    assert restored['id']==row['id'] and restored['source_text']==row['source_text']
    assert reader.read({'month':['']})['totals']['SGD']=='7.50'
    assert repo.count_transactions()==1 and len(repo.recent_transactions(42))==1
    assert reader.read({'state':['deleted'],'month':['']})['total']==0
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM transactions').fetchone()[0]==1
        assert conn.execute('SELECT action FROM transaction_deletions ORDER BY id').fetchall()==[('delete',),('restore',)]


def test_owner_stale_edits_and_delete_restore_replay_are_blocked(tmp_path):
    repo,reader,row=manual(tmp_path)
    with pytest.raises(RecordConflictError):change_deletion(repo,84,row['id'],row['revision'],'delete')
    expense=repo.get_transaction(row['id']).expense
    change_deletion(repo,42,row['id'],row['revision'],'delete')
    with pytest.raises(RecordConflictError):repo.edit_record('transactions',row['id'],42,expense,row['revision'])
    deleted=reader.read({'state':['deleted'],'month':['']})['records'][0]
    change_deletion(repo,42,row['id'],deleted['revision'],'restore')
    with pytest.raises(RecordConflictError):change_deletion(repo,42,row['id'],row['revision'],'delete')
    with pytest.raises(RecordConflictError):change_deletion(repo,42,row['id'],deleted['revision'],'restore')
    assert reader.read({'month':['']})['records'][0]['revision']!=row['revision']


def test_concurrent_delete_creates_one_tombstone(tmp_path):
    repo,reader,row=manual(tmp_path)
    def perform(_):
        try:change_deletion(repo,42,row['id'],row['revision'],'delete');return 'ok'
        except RecordConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(perform,range(2)))==['conflict','ok']
    assert reader.read({'state':['deleted'],'month':['']})['total']==1


def test_restore_database_failure_keeps_deleted_record_and_audit_atomic(tmp_path):
    repo,reader,row=manual(tmp_path)
    change_deletion(repo,42,row['id'],row['revision'],'delete')
    deleted=reader.read({'state':['deleted'],'month':['']})['records'][0]
    with sqlite3.connect(repo._path) as conn:
        conn.execute("CREATE TRIGGER synthetic_restore_failure BEFORE DELETE ON deleted_transactions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):change_deletion(repo,42,row['id'],deleted['revision'],'restore')
    assert reader.read({'state':['deleted'],'month':['']})['records'][0]['revision']==deleted['revision']
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM transaction_deletions').fetchone()[0]==1


def import_bodies(data):
    return [{'row_key':r['row_key'],'action':'import','category':'shopping','is_personal':True} for r in data['rows']]


def test_uob_cleanup_only_imports_preserves_manual_and_other_owner_and_is_restorable(tmp_path):
    repo,data=setup(tmp_path,3);prepare_deletions(repo);add(repo,amount='9.00')
    manual_record=repo.get_transaction(1)
    confirm_rows(repo,42,data,import_bodies(data))
    other_id=create_review(repo,84,data);other=open_review(repo,84,other_id)
    confirm_rows(repo,84,other,[import_bodies(other)[0]])
    plan=uob_import_plan(repo,42);assert len(plan)==3
    backup=backup_ledger(repo,'before-uob-withdrawal')
    assert backup.stat().st_mode&0o777==0o600
    assert withdraw_uob_imports(repo,42,plan)==3
    assert repo.get_transaction(1)==manual_record
    assert len(repo.recent_transactions(42))==1 and len(repo.recent_transactions(84))==1
    assert withdraw_uob_imports(repo,42,{})==0
    reader=LedgerReader(repo._path,42)
    deleted=reader.read({'state':['deleted'],'month':['']})['records']
    assert len(deleted)==3 and all(r['deletion_reason']=='uob_reset' for r in deleted)
    assert reader.read({'month':['']})['totals']['SGD']=='9.00'
    row=deleted[0];change_deletion(repo,42,row['id'],row['revision'],'restore')
    assert repo.count_transactions()==3
    assert reader.read({'month':['']})['total']==2


def test_cleanup_wrong_plan_or_partial_failure_does_not_change_any_record(tmp_path):
    repo,data=setup(tmp_path,3);prepare_deletions(repo);confirm_rows(repo,42,data,import_bodies(data))
    plan=uob_import_plan(repo,42)
    with pytest.raises(RecordConflictError):withdraw_uob_imports(repo,42,{})
    blocked=sorted(plan)[1]
    with sqlite3.connect(repo._path) as conn:
        conn.execute(f"CREATE TRIGGER synthetic_cleanup_failure BEFORE INSERT ON deleted_transactions WHEN NEW.transaction_id={blocked} BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):withdraw_uob_imports(repo,42,plan)
    assert uob_import_plan(repo,42)==plan and repo.count_transactions()==3
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM transaction_deletions').fetchone()[0]==0


def test_legacy_schema_backup_precedes_lifecycle_column(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db');repo.initialize();add(repo)
    with sqlite3.connect(repo._path) as conn:conn.execute('ALTER TABLE transactions DROP COLUMN lifecycle_version')
    prepare_deletions(repo);prepare_deletions(repo)
    paths=list((tmp_path/'backups').glob('*before-recoverable-delete-*.db'))
    assert len(paths)==1
    with sqlite3.connect(paths[0]) as conn:
        assert 'lifecycle_version' not in {r[1] for r in conn.execute('PRAGMA table_info(transactions)')}
        assert conn.execute('SELECT count(*) FROM transactions').fetchone()[0]==1


def test_http_delete_restore_requires_auth_csrf_and_matching_revision(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        add(repo);row=reader.read({'month':['']})['records'][0]
        url=f"/api/transactions/{row['id']}/delete"
        assert client.post(url,json={'revision':row['revision']}).status_code==401
        login(client)
        assert client.post(url,json={'revision':row['revision']},headers={'X-CSRF-Token':'bad'}).status_code==403
        assert client.post(url,json={'revision':row['revision']}).status_code==200
        assert client.post(url,json={'revision':row['revision']}).status_code==409
        deleted=client.get('/api/ledger?state=deleted&month=').json()['records'][0]
        assert client.post(f"/api/transactions/{row['id']}/restore",json={'revision':deleted['revision']}).status_code==200
        assert repo.count_transactions()==1


def test_paused_statement_api_and_default_setting(tmp_path):
    repo,reader,row=manual(tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(reader,statements_enabled=False))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
            client.headers['Origin']=str(client.base_url).rstrip('/');login(client)
            assert client.get('/api/ledger').json()['features']['statements'] is False
            assert client.get('/api/statements').status_code==403
            for route in ['preview','resume','progress','confirm','confirm-batch','correct','card-default']:
                assert client.post('/api/statements/'+route,json={}).status_code==403
            assert client.get('/api/ledger?state=deleted').status_code==200
    finally:server.shutdown();server.server_close();thread.join()
    assert WebSettings(telegram_allowed_user_ids='42',_env_file=None).statement_upload_enabled is False
