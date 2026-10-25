# 검토 진행 저장과 재시작 복원, 충돌 및 소유자 경계를 합성 자료로 검증합니다.
import copy
import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pydantic import ValidationError

import pytest

from finance_bot.database import FinanceRepository, RecordConflictError
from finance_bot.reconciliation import prepare_links, confirm_rows
from finance_bot.statement_reviews import (prepare_reviews, create_review, open_review,
    save_progress, list_reviews, review_by_source)
from finance_bot.web import make_handler, LedgerReader
from test_statement_batch import batch_preview
from test_reconciliation import upload
from test_web_editing import running, login
from test_web import add


def setup(tmp_path, count=3):
    repo=FinanceRepository(tmp_path/'ledger.db');repo.initialize();repo.prepare_web_edits()
    prepare_links(repo);prepare_reviews(repo)
    data=batch_preview(count)
    review_id=create_review(repo,42,data)
    return repo,open_review(repo,42,review_id)


def state(data):
    return {'phase':'review','choices':{r['row_key']:{'action':'import','category':'shopping',
        'is_personal':False,'reviewed':True,'deferred':False,'acknowledge':False,'dirty':True}
        for r in data['rows'] if r['kind']=='charge' and not r['processed']},
        'applied_cards':[data['cards'][0]['account']],
        'card_drafts':{data['cards'][0]['account']:{'personal':False,'remember':True}}}


def test_progress_survives_new_repository_without_ledger_writes(tmp_path):
    repo,data=setup(tmp_path);body=state(data)
    body['choices'][data['rows'][1]['row_key']]['deferred']=True
    version=save_progress(repo,42,data,body)
    restarted=FinanceRepository(repo._path)
    restored=open_review(restarted,42,data['review_id'])
    assert restored['progress_version']==version==2
    assert restored['review_state']['phase']=='review'
    assert restored['review_state']['choices'][data['rows'][1]['row_key']]['deferred'] is True
    assert restored['review_state']['card_drafts']==body['card_drafts']
    assert restored['review_state']['applied_cards']==body['applied_cards']
    assert restarted.count_transactions()==0
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM drafts').fetchone()[0]==0


def test_same_pdf_resumes_per_owner_and_list_is_isolated(tmp_path):
    repo,data=setup(tmp_path)
    assert create_review(repo,42,data)==data['review_id']
    assert review_by_source(repo,84,data['source_sha256']) is None
    other=create_review(repo,84,data)
    assert other!=data['review_id']
    assert list_reviews(repo,42)['total']==1
    assert list_reviews(repo,84)['items'][0]['id']==other
    with pytest.raises(RecordConflictError):open_review(repo,84,data['review_id'])


def test_stale_saves_and_confirmations_fail_without_changing_anything(tmp_path):
    repo,data=setup(tmp_path)
    save_progress(repo,42,data,state(data))
    with pytest.raises(RecordConflictError):save_progress(repo,42,data,state(data))
    item={'row_key':data['rows'][0]['row_key'],'action':'import','category':'dining','is_personal':True}
    with pytest.raises(RecordConflictError):confirm_rows(repo,42,data,[item])
    assert repo.count_transactions()==0
    assert open_review(repo,42,data['review_id'])['progress_version']==2


def test_concurrent_saves_allow_one_winner(tmp_path):
    repo,data=setup(tmp_path)
    def save():
        try:save_progress(repo,42,data,state(data));return 'ok'
        except RecordConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:save(),range(2)))==['conflict','ok']


def test_confirmation_failure_does_not_advance_progress_version(tmp_path):
    repo,data=setup(tmp_path)
    items=[{'row_key':r['row_key'],'action':'import','category':'shopping','is_personal':True} for r in data['rows']]
    items[-1]['category']='invalid'
    with pytest.raises(ValidationError):confirm_rows(repo,42,data,items)
    assert repo.count_transactions()==0
    assert open_review(repo,42,data['review_id'])['progress_version']==1


def test_history_pagination_stays_owner_scoped(tmp_path):
    repo,data=setup(tmp_path)
    for index in range(25):
        extra=copy.deepcopy(data);extra['source_sha256']=f'{index:064x}'
        create_review(repo,42,extra)
    assert list_reviews(repo,42)['total']==26
    assert len(list_reviews(repo,42)['items'])==25
    assert len(list_reviews(repo,42,2)['items'])==1
    assert list_reviews(repo,84)['total']==0


@pytest.mark.parametrize('change',[
    lambda b:b['choices'].update({'not-a-row':next(iter(b['choices'].values()))}),
    lambda b:next(iter(b['choices'].values())).update(is_personal='false'),
    lambda b:next(iter(b['choices'].values())).update(category='invalid'),
    lambda b:next(iter(b['choices'].values())).update(action='9999'),
    lambda b:next(iter(b['choices'].values())).update(amount='0.01'),
    lambda b:b.update(phase='saved'),
    lambda b:b['card_drafts'].update({'unknown-card':{'personal':True,'remember':False}}),
])
def test_malformed_or_forged_progress_never_replaces_bank_facts(tmp_path,change):
    repo,data=setup(tmp_path);body=state(data);change(body)
    with pytest.raises(ValueError):save_progress(repo,42,data,body)
    fresh=open_review(repo,42,data['review_id'])
    assert fresh['progress_version']==1
    assert fresh['rows'][0]['amount']==data['rows'][0]['amount']


def test_partial_completion_deferred_counts_and_completed_status(tmp_path):
    repo,data=setup(tmp_path);body=state(data)
    body['choices'][data['rows'][1]['row_key']]['deferred']=True
    save_progress(repo,42,data,body);data=open_review(repo,42,data['review_id'])
    def item(row):return {'row_key':row['row_key'],'action':'import','category':'shopping','is_personal':False}
    confirm_rows(repo,42,data,[item(data['rows'][0]),item(data['rows'][2])])
    summary=list_reviews(repo,42)['items'][0]
    assert (summary['status'],summary['confirmed_count'],summary['deferred_count'])==('partial',2,1)
    data=open_review(repo,42,data['review_id'])
    assert data['progress_version']==3
    confirm_rows(repo,42,data,[item(data['rows'][1])])
    summary=list_reviews(repo,42)['items'][0]
    assert (summary['status'],summary['confirmed_count'],summary['deferred_count'])==('complete',3,0)


def test_changed_ledger_candidates_invalidate_saved_approval(tmp_path):
    repo,data=setup(tmp_path,1);body=state(data)
    body['choices'][data['rows'][0]['row_key']]['acknowledge']=True
    save_progress(repo,42,data,body)
    from datetime import date
    add(repo,amount='0.01')
    reader=LedgerReader(repo._path,42);record=reader.read({'month':['']})['records'][0]
    expense=repo.get_transaction(1).expense.model_copy(update={'transaction_date':date(2026,10,1),'payment_account':'SG UOB 4444'})
    repo.edit_record('transactions',1,42,expense,record['revision'])
    fresh=open_review(repo,42,data['review_id'])
    choice=fresh['review_state']['choices'][data['rows'][0]['row_key']]
    assert choice['action']=='' and choice['reviewed'] is False and choice['acknowledge'] is False
    assert '记账记录有变化' in ''.join(fresh['rows'][0]['suggestion']['reasons'])


def test_migration_backup_is_private_and_created_once(tmp_path):
    repo,data=setup(tmp_path)
    prepare_reviews(repo)
    paths=list((tmp_path/'backups').glob('*before-review-progress-*.db'))
    assert len(paths)==1 and paths[0].stat().st_mode&0o777==0o600
    with sqlite3.connect(paths[0]) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_reviews'").fetchone() is None
    with sqlite3.connect(repo._path) as conn:
        raw=conn.execute('SELECT preview_json FROM statement_reviews').fetchone()[0]
    assert 'pdf_base64' not in raw and 'candidates' not in raw and 'preview_id' not in raw


def test_http_progress_relogin_and_restart(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        assert client.get('/api/statements').status_code==401
        assert client.post('/api/statements/progress',json={}).status_code==401
        login(client)
        data=upload(client);body={**state(data),'preview_id':data['preview_id']}
        assert client.post('/api/statements/progress',json=body,headers={'X-CSRF-Token':'wrong'}).status_code==403
        saved=client.post('/api/statements/progress',json=body)
        assert saved.status_code==200
        assert client.post('/api/statements/progress',json=body).status_code==409
        assert client.post('/api/logout',json={}).status_code==200
        result=client.post('/api/login',json={'password':'synthetic-test-password'})
        client.headers['X-CSRF-Token']=result.json()['csrf']
        restored=client.post('/api/statements/resume',json={'review_id':data['review_id']}).json()
        assert restored['review_state']['phase']=='review'
        assert upload(client)['review_id']==data['review_id']
        assert client.get('/api/statements').json()['total']==1
        assert repo.count_transactions()==0
    with running(tmp_path) as (repo,reader,client):
        result=client.post('/api/login',json={'password':'synthetic-test-password'})
        client.headers['X-CSRF-Token']=result.json()['csrf']
        resumed=client.post('/api/statements/resume',json={'review_id':data['review_id']})
        assert resumed.status_code==200 and resumed.json()['review_state']['choices']
        assert client.get('/api/statements?page=0').status_code==400
        assert repo.count_transactions()==0


def test_large_progress_body_and_same_pdf_skips_reparse(monkeypatch,tmp_path):
    with running(tmp_path) as (repo,reader,client):
        login(client)
        monkeypatch.setattr('finance_bot.web.preview_pdf',lambda _:batch_preview(1000))
        upload_body={'pdf_base64':base64.b64encode(b'%PDF-large-synthetic').decode()}
        data=client.post('/api/statements/preview',json=upload_body).json()
        body={**state(data),'preview_id':data['preview_id']}
        assert 32768<len(json.dumps(body))<512*1024
        assert client.post('/api/statements/progress',json=body,headers={'X-CSRF-Token':'bad'}).status_code==403
        assert client.post('/api/statements/progress',json=body).status_code==200
        def no_reparse(_):raise AssertionError('same PDF should resume saved metadata')
        monkeypatch.setattr('finance_bot.web.preview_pdf',no_reparse)
        again=client.post('/api/statements/preview',json=upload_body)
        assert again.status_code==200
        assert again.json()['review_id']==data['review_id']
        assert len(again.json()['review_state']['choices'])==1000
        assert repo.count_transactions()==0
