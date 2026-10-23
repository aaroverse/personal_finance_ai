# 합성 명세서의 원자적 대조와 중복 방지 및 개인 지출 집계를 검증합니다.
import base64
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from finance_bot.database import FinanceRepository, RecordConflictError
from finance_bot.reconciliation import PreviewStore, confirm_row, identify_rows, prepare_links
from finance_bot.statements import parse_uob_text, suggest_matches
from finance_bot.web import LedgerReader
from test_statements import SAMPLE, pdf_bytes
from test_web import add
from test_web_editing import running, login


def upload(client, text=SAMPLE):
    response = client.post('/api/statements/preview', json={'pdf_base64':base64.b64encode(pdf_bytes(text)).decode()})
    assert response.status_code == 200
    return response.json()


def request_for(preview, **kwargs):
    row = next(r for r in preview['rows'] if r['kind'] == 'charge')
    return {'preview_id':preview['preview_id'], 'row_key':row['row_key'], 'action':'import',
        'category':'dining', 'is_personal':False, **kwargs}


def test_import_nonpersonal_repeat_upload_and_reversible_attribution(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        login(client)
        preview = upload(client)
        body = request_for(preview)
        result = client.post('/api/statements/confirm', json=body)
        assert result.status_code == 200
        assert result.json()['is_personal'] is False
        assert client.post('/api/statements/confirm', json=body).status_code == 409
        again = upload(client)
        charge = next(r for r in again['rows'] if r['kind'] == 'charge')
        assert charge['processed']['transaction_id'] == result.json()['transaction_id']
        assert repo.count_transactions() == 1
        data = reader.read({'month':['']})
        assert data['totals']['SGD'] == '0.00' and data['categories'] == []
        row = data['records'][0]
        assert row['statement']['merchant'] == 'KOPITIAM TEST'
        assert row['statement']['original_currency'] == 'TWD'
        expense = {k:row[k] for k in ['transaction_date','amount','currency','merchant','category','payment_account','note','is_personal']}
        expense['is_personal'] = True
        assert client.post(f"/api/transactions/{row['id']}/edit",json={'revision':row['revision'],'expense':expense}).status_code == 200
        assert reader.read({'month':['']})['totals']['SGD'] == '12.50'
        updated = reader.read({'month':['']})['records'][0]
        expense['amount'] = '13.00'
        assert client.post(f"/api/transactions/{row['id']}/edit",json={'revision':updated['revision'],'expense':expense}).status_code == 409


def test_match_preserves_user_source_category_and_updates_personal_flag(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        add(repo, amount='12.50')
        transaction = repo.get_transaction(1)
        changed = transaction.expense.model_copy(update={'transaction_date':date(2026,10,1)})
        row = reader.read({'month':['']})['records'][0]
        repo.edit_record('transactions',1,42,changed,row['revision'])
        login(client)
        preview = upload(client)
        charge = next(r for r in preview['rows'] if r['kind']=='charge')
        candidate = charge['candidates'][0]
        body = request_for(preview, action='match',transaction_id=candidate['id'],revision=candidate['revision'],category='shopping')
        assert client.post('/api/statements/confirm',json=body).status_code == 200
        saved = reader.read({'month':['']})['records'][0]
        assert repo.count_transactions() == 1
        assert saved['merchant'] == '午餐' and saved['source_text'].startswith('原始描述')
        assert saved['statement']['merchant'] == 'KOPITIAM TEST'
        assert saved['category']=='shopping' and saved['is_personal'] is False
        with sqlite3.connect(reader.path) as conn:
            assert conn.execute('SELECT count(*) FROM statement_links').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM ledger_edits').fetchone()[0] == 2


def test_stale_candidate_wrong_owner_and_unacknowledged_duplicate_fail_closed(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        add(repo, amount='12.50')
        row = reader.read({'month':['']})['records'][0]
        expense = repo.get_transaction(1).expense.model_copy(update={'transaction_date':date(2026,10,1)})
        repo.edit_record('transactions',1,42,expense,row['revision'])
        login(client)
        preview=upload(client)
        body=request_for(preview)
        assert client.post('/api/statements/confirm',json=body).status_code == 409
        candidate=next(r for r in preview['rows'] if r['kind']=='charge')['candidates'][0]
        bad=request_for(preview,action='match',transaction_id=999,revision=candidate['revision'])
        assert client.post('/api/statements/confirm',json=bad).status_code==409
        current=reader.read({'month':['']})['records'][0]
        repo.edit_record('transactions',1,42,expense.model_copy(update={'merchant':'新名称'}),current['revision'])
        stale=request_for(preview,action='match',transaction_id=1,revision=candidate['revision'])
        assert client.post('/api/statements/confirm',json=stale).status_code==409
        body['acknowledge_possible_duplicate']=True
        assert client.post('/api/statements/confirm',json=body).status_code==200
        assert repo.count_transactions()==2


def test_invalid_bool_credits_bad_balance_csrf_and_preview_tampering(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        login(client)
        preview=upload(client)
        body=request_for(preview)
        assert client.post('/api/statements/confirm',json=body,headers={'X-CSRF-Token':'wrong'}).status_code==403
        assert client.post('/api/statements/confirm',json={**body,'preview_id':'forged'}).status_code==409
        assert client.post('/api/statements/confirm',json={**body,'row_key':'forged'}).status_code==400
        assert client.post('/api/statements/confirm',json={**body,'is_personal':'false'}).status_code==400
        assert client.post('/api/statements/confirm',json={**body,'category':'not-real'}).status_code==422
        for row in preview['rows']:
            if row['kind']!='charge':
                assert client.post('/api/statements/confirm',json={**body,'row_key':row['row_key']}).status_code==400
        bad=upload(client,SAMPLE.replace('SUB TOTAL 10.00','SUB TOTAL 11.00'))
        assert client.post('/api/statements/confirm',json=request_for(bad)).status_code==400
        assert repo.count_transactions()==0


def test_preview_is_session_bound_and_expires(monkeypatch):
    store=PreviewStore()
    token=store.put('session-one',{'test':'metadata'})
    assert store.get(token,'session-one')=={'test':'metadata'}
    with pytest.raises(RecordConflictError):
        store.get(token,'session-two')
    monkeypatch.setattr('finance_bot.reconciliation.time.monotonic',lambda:float('inf'))
    with pytest.raises(RecordConflictError):
        store.get(token,'session-one')


def test_one_transaction_cannot_be_matched_to_two_distinct_statement_rows(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        add(repo,amount='12.50')
        original=reader.read({'month':['']})['records'][0]
        expense=repo.get_transaction(1).expense.model_copy(update={'transaction_date':date(2026,10,1)})
        repo.edit_record('transactions',1,42,expense,original['revision'])
        login(client)
        sample=SAMPLE.replace('SUB TOTAL 10.00','03 OCT 01 OCT KOPITIAM TEST 12.50\nSUB TOTAL 22.50')
        preview=upload(client,sample)
        rows=[r for r in preview['rows'] if r['kind']=='charge']
        candidate=rows[0]['candidates'][0]
        body=request_for(preview,action='match',transaction_id=1,revision=candidate['revision'],is_personal=True)
        assert client.post('/api/statements/confirm',json=body).status_code==200
        assert client.post('/api/statements/confirm',json={**body,'row_key':rows[1]['row_key']}).status_code==409
        assert repo.count_transactions()==1


def test_new_login_cannot_confirm_an_old_session_preview(tmp_path):
    with running(tmp_path) as (repo,reader,client):
        login(client)
        body=request_for(upload(client))
        assert client.post('/api/logout',json={}).status_code==200
        result=client.post('/api/login',json={'password':'synthetic-test-password'})
        client.headers['X-CSRF-Token']=result.json()['csrf']
        assert client.post('/api/statements/confirm',json=body).status_code==409
        assert repo.count_transactions()==0


def test_identical_rows_are_distinct_but_reprocessing_and_concurrent_import_cannot_duplicate(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db')
    repo.initialize(); repo.prepare_web_edits(); prepare_links(repo)
    sample=SAMPLE.replace('SUB TOTAL 10.00','03 OCT 01 OCT KOPITIAM TEST 12.50\nSUB TOTAL 22.50')
    data=parse_uob_text([sample]); identify_rows(data,b'synthetic-source')
    data=suggest_matches(data,repo._path,42)
    charges=[r for r in data['rows'] if r['kind']=='charge']
    assert charges[0]['row_key']!=charges[1]['row_key']
    body={'row_key':charges[0]['row_key'],'action':'import','category':'dining','is_personal':False}
    def attempt(_):
        try:
            confirm_row(repo,42,data,body)
            return 'saved'
        except RecordConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==['conflict','saved']
    assert repo.count_transactions()==1
    different=copy.deepcopy(data); identify_rows(different,b'different-PDF-metadata')
    assert charges[0]['row_key']==different['rows'][1]['row_key']
    with pytest.raises(RecordConflictError):
        confirm_row(repo,42,different,body)
    confirm_row(repo,42,data,{**body,'row_key':charges[1]['row_key'],'acknowledge_possible_duplicate':True})
    assert repo.count_transactions()==2


def test_backup_precedes_new_schema_and_is_created_once(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db'); repo.initialize(); repo.prepare_web_edits()
    add(repo)
    prepare_links(repo); prepare_links(repo)
    backups=list((tmp_path/'backups').glob('*before-statement-links-*.db'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute('SELECT count(*) FROM transactions').fetchone()[0]==1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_links'").fetchone()


def test_legacy_draft_json_default_and_callback_revision_stay_valid(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db'); repo.initialize()
    draft=add(repo,pending=True)
    with sqlite3.connect(repo._path) as conn:
        old=json.loads(conn.execute('SELECT expense_json FROM drafts WHERE id=?',(draft.id,)).fetchone()[0])
        old.pop('is_personal')
        conn.execute('UPDATE drafts SET expense_json=? WHERE id=?',(json.dumps(old),draft.id))
    legacy=repo.get_draft(draft.id)
    assert legacy.expense.is_personal is True
    assert repo.confirm_draft(draft.id,42,legacy.revision).expense.is_personal is True


@pytest.mark.asyncio
async def test_telegram_redescription_preserves_user_personal_choice(tmp_path):
    from test_service import make_service
    service,repo,_=make_service(tmp_path)
    draft=await service.submit_expense(42,'MRT')
    repo.prepare_web_edits()
    repo.edit_record('drafts',draft.id,42,draft.expense.model_copy(update={'is_personal':False}),draft.revision)
    updated=await service.revise_expense(42,draft.id,'MRT changed description')
    assert updated.expense.is_personal is False


@pytest.mark.asyncio
async def test_ai_cannot_set_personal_attribution():
    from test_extractor import settings
    from finance_bot.extractor import DeepSeekExpenseExtractor
    import httpx
    # Validate response locally; no request leaves the process.
    async with httpx.AsyncClient() as client:
        extractor=DeepSeekExpenseExtractor(settings(), client)
        raw={'transaction_date':'2026-10-01','amount':'12.50','currency':'SGD','merchant':'Lunch','category':'dining',
            'payment_account':'SG UOB','confidence':1,'needs_review':False,'ambiguities':[],'is_personal':False}
        assert extractor._validate_content(json.dumps(raw)).is_personal is True
