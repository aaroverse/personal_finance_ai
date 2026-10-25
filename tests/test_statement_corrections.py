# 명세서 정정의 소유권, 감사 이력, 소프트 취소와 재검토 경계를 검증합니다.
import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from finance_bot.database import RecordConflictError
from finance_bot.reconciliation import confirm_rows
from finance_bot.statement_corrections import prepare_corrections, correct_row
from finance_bot.statement_reviews import open_review, create_review, list_reviews, save_progress
from finance_bot.web import LedgerReader
from test_statement_reviews import setup, state
from test_reconciliation import upload, request_for
from test_web_editing import running, login
from test_web import add


def imported(tmp_path):
    repo,data=setup(tmp_path,1);prepare_corrections(repo)
    confirm_rows(repo,42,data,[entry(data)])
    return repo,open_review(repo,42,data['review_id'])


def entry(data,**extra):
    row=next(r for r in data['rows'] if r['kind']=='charge')
    return {'row_key':row['row_key'],'action':'import','category':'dining','is_personal':True,**extra}


def correction(data,**extra):
    row=next(r for r in data['rows'] if r['kind']=='charge');saved=row['processed']
    return {'row_key':row['row_key'],'transaction_id':saved['transaction_id'],'link_id':saved['link_id'],
        'revision':saved['revision'],'operation':'unlink' if saved['action']=='match' else 'void_import',**extra}


def test_void_import_preserves_original_excludes_every_active_surface_and_can_reenter(tmp_path):
    repo,data=imported(tmp_path);transaction_id=data['rows'][0]['processed']['transaction_id']
    reader=LedgerReader(repo._path,42)
    assert reader.read({'month':['']})['totals']['SGD']=='0.01'
    correct_row(repo,42,data,correction(data))
    assert repo.count_transactions()==0 and repo.recent_transactions(42)==[]
    with pytest.raises(LookupError):repo.get_transaction(transaction_id)
    assert reader.read({'month':['']})['totals']['SGD']=='0.00'
    assert reader.read({'month':['']})['records']==[]
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM transactions').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM drafts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM statement_links').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM voided_transactions').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM statement_corrections').fetchone()[0]==1
        assert 'expense_json' not in conn.execute('SELECT link_json FROM statement_corrections').fetchone()[0]
    fresh=open_review(repo,42,data['review_id']);row=fresh['rows'][0]
    assert row['requires_recheck'] and row['suggestion']['ready'] is False
    assert row['candidate_count']==0 and row['possible_duplicate_count']==0
    assert row['correction_history'][0]['transaction_id']==transaction_id
    assert list_reviews(repo,42)['items'][0]['status']=='pending'
    with pytest.raises(RecordConflictError):confirm_rows(repo,42,fresh,[entry(fresh)])
    confirm_rows(repo,42,fresh,[entry(fresh,acknowledge_correction=True)])
    assert repo.count_transactions()==1
    assert repo.recent_transactions(42)[0].id!=transaction_id
    assert reader.read({'month':['']})['totals']['SGD']=='0.01'


def test_unlink_match_keeps_original_record_current_category_and_attribution(tmp_path):
    repo,data=setup(tmp_path,1);prepare_corrections(repo)
    add(repo,amount='0.01');reader=LedgerReader(repo._path,42)
    row=reader.read({'month':['']})['records'][0]
    expense=repo.get_transaction(1).expense.model_copy(update={'transaction_date':date(2026,10,1),'payment_account':'SG UOB 4444'})
    repo.edit_record('transactions',1,42,expense,row['revision'])
    data=open_review(repo,42,data['review_id']);candidate=data['rows'][0]['candidates'][0]
    confirm_rows(repo,42,data,[entry(data,action='match',transaction_id=candidate['id'],revision=candidate['revision'],category='shopping',is_personal=False)])
    data=open_review(repo,42,data['review_id'])
    with pytest.raises(ValueError):correct_row(repo,42,data,correction(data,operation='void_import'))
    correct_row(repo,42,data,correction(data))
    saved=reader.read({'month':['']})['records'][0]
    assert repo.count_transactions()==1 and saved['statement'] is None
    assert saved['category']=='shopping' and saved['is_personal'] is False
    assert saved['merchant']=='午餐'
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM voided_transactions').fetchone()[0]==0
    fresh=open_review(repo,42,data['review_id'])
    assert fresh['rows'][0]['candidate_count']==1
    assert fresh['rows'][0]['requires_recheck'] is True


def test_old_edits_cannot_modify_voided_import(tmp_path):
    repo,data=imported(tmp_path);reader=LedgerReader(repo._path,42)
    record=reader.read({'month':['']})['records'][0];expense=repo.get_transaction(record['id']).expense
    correct_row(repo,42,data,correction(data))
    with pytest.raises(RecordConflictError):repo.edit_record('transactions',record['id'],42,expense,record['revision'])


def test_voiding_nonpersonal_import_does_not_reduce_personal_total(tmp_path):
    repo,data=setup(tmp_path,1);prepare_corrections(repo)
    confirm_rows(repo,42,data,[entry(data,is_personal=False)])
    data=open_review(repo,42,data['review_id']);reader=LedgerReader(repo._path,42)
    assert reader.read({'month':['']})['totals']['SGD']=='0.00'
    correct_row(repo,42,data,correction(data))
    assert reader.read({'month':['']})['totals']['SGD']=='0.00'
    assert repo.count_transactions()==0


def test_wrong_owner_ids_revision_and_replay_are_rejected(tmp_path):
    repo,data=imported(tmp_path)
    with pytest.raises(RecordConflictError):correct_row(repo,84,data,correction(data))
    for change in [{'transaction_id':999},{'link_id':999},{'revision':'0'*64}]:
        with pytest.raises(RecordConflictError):correct_row(repo,42,data,correction(data,**change))
    for change in [{'revision':None},{'operation':'unlink'},{'transaction_id':True}]:
        with pytest.raises(ValueError):correct_row(repo,42,data,correction(data,**change))
    correct_row(repo,42,data,correction(data))
    with pytest.raises(RecordConflictError):correct_row(repo,42,data,correction(data))
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM statement_corrections').fetchone()[0]==1


def test_edit_after_preview_blocks_correction(tmp_path):
    repo,data=imported(tmp_path);reader=LedgerReader(repo._path,42)
    record=reader.read({'month':['']})['records'][0]
    expense=repo.get_transaction(record['id']).expense.model_copy(update={'category':'shopping'})
    repo.edit_record('transactions',record['id'],42,expense,record['revision'])
    with pytest.raises(RecordConflictError):correct_row(repo,42,data,correction(data))
    assert repo.count_transactions()==1


def test_concurrent_corrections_only_create_one_audit(tmp_path):
    repo,data=imported(tmp_path)
    def perform(_):
        try:correct_row(repo,42,data,correction(data));return 'ok'
        except RecordConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(perform,range(2)))==['conflict','ok']
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM statement_corrections').fetchone()[0]==1


def test_database_failure_rolls_back_audit_link_void_and_version(tmp_path):
    repo,data=imported(tmp_path)
    with sqlite3.connect(repo._path) as conn:
        conn.execute("CREATE TRIGGER synthetic_block_void BEFORE INSERT ON voided_transactions BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):correct_row(repo,42,data,correction(data))
    assert repo.count_transactions()==1
    fresh=open_review(repo,42,data['review_id'])
    assert fresh['progress_version']==data['progress_version'] and fresh['rows'][0]['processed']
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM statement_corrections').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM voided_transactions').fetchone()[0]==0


def test_old_approval_in_another_pdf_cannot_reenter_after_correction_cycles(tmp_path):
    repo,data=imported(tmp_path)
    second=copy.deepcopy(data);second['source_sha256']='f'*64
    second_id=create_review(repo,42,second)
    stale=open_review(repo,42,second_id)
    correct_row(repo,42,data,correction(data))
    fresh=open_review(repo,42,data['review_id'])
    confirm_rows(repo,42,fresh,[entry(fresh,acknowledge_correction=True)])
    current=open_review(repo,42,data['review_id']);correct_row(repo,42,current,correction(current))
    with pytest.raises(RecordConflictError):confirm_rows(repo,42,stale,[entry(stale,acknowledge_correction=True)])
    assert repo.count_transactions()==0
    latest=open_review(repo,42,second_id)
    assert latest['rows'][0]['correction_count']==2


def test_schema_backup_and_old_progress_remain_intact(tmp_path):
    repo,data=setup(tmp_path,1);save_progress(repo,42,data,state(data))
    prepare_corrections(repo);prepare_corrections(repo)
    paths=list((tmp_path/'backups').glob('*before-statement-corrections-*.db'))
    assert len(paths)==1 and paths[0].stat().st_mode&0o777==0o600
    with sqlite3.connect(paths[0]) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='voided_transactions'").fetchone() is None
    fresh=open_review(repo,42,data['review_id'])
    assert fresh['review_state']['choices'][data['rows'][0]['row_key']]['action']=='import'


def test_http_correction_auth_csrf_history_and_explicit_recheck(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        assert client.post('/api/statements/correct',json={}).status_code==401
        login(client);data=upload(client)
        saved=client.post('/api/statements/confirm',json=request_for(data,is_personal=True)).json()['preview']
        body={**correction(saved),'preview_id':saved['preview_id']}
        assert client.post('/api/statements/correct',json=body,headers={'X-CSRF-Token':'bad'}).status_code==403
        assert client.post('/api/statements/correct',json=body,headers={'Origin':'https://evil.example'}).status_code==403
        result=client.post('/api/statements/correct',json=body)
        assert result.status_code==200
        fresh=result.json()['preview'];charge=next(r for r in fresh['rows'] if r['kind']=='charge')
        assert charge['processed'] is None and charge['requires_recheck']
        assert repo.count_transactions()==0 and reader.read({'month':['']})['totals']['SGD']=='0.00'
        assert client.post('/api/statements/correct',json=body).status_code==409
        assert client.post('/api/statements/confirm',json=request_for(fresh)).status_code==409
        body=request_for(fresh,acknowledge_correction=True,is_personal=True)
        assert client.post('/api/statements/confirm',json=body).status_code==200
        assert repo.count_transactions()==1
