# 대량 명세서의 로컬 제안과 일괄 확인 및 카드 귀속 설정을 합성 데이터로 검증합니다.
import base64
import copy
import json
import sqlite3

import pytest
from pydantic import ValidationError

from finance_bot.database import FinanceRepository, RecordConflictError
from finance_bot.reconciliation import identify_rows, prepare_links, confirm_rows, save_card_default
from finance_bot.statements import parse_uob_text, suggest_matches
from test_statements import SAMPLE
from test_web_editing import running, login


def batch_preview(count=3):
    data=parse_uob_text([SAMPLE])
    template=data['rows'][1]
    data['rows']=[{**copy.deepcopy(template),'id':i,'merchant':f'FAIRPRICE TEST {i}',
        'amount':f'{i//100}.{i%100:02}','signed_amount':f'{i//100}.{i%100:02}'} for i in range(1,count+1)]
    total=sum(range(1,count+1))
    data['cards'][0].update(opening_balance='0.00',closing_balance=f'{total//100}.{total%100:02}',
        net_movement=f'{total//100}.{total%100:02}',row_count=count,balance_ok=True)
    identify_rows(data,b'synthetic-batch-only')
    return data


def make_repo(tmp_path):
    repo=FinanceRepository(tmp_path/'ledger.db');repo.initialize();repo.prepare_web_edits();prepare_links(repo)
    return repo


def bodies(data):
    return [{'row_key':r['row_key'],'action':'import','category':r['suggestion']['category'],'is_personal':False}
        for r in data['rows']]


def test_local_rules_unknown_merchant_and_intra_statement_duplicates(tmp_path):
    repo=make_repo(tmp_path)
    data=batch_preview()
    data['rows'][1]['merchant']='UNRECOGNIZED MERCHANT'
    data['rows'][2].update(merchant='FAIRPRICE TEST 1',amount='0.01',signed_amount='0.01')
    data=suggest_matches(data,repo._path,42)
    assert data['preprocessing']['ai_used'] is False
    assert data['rows'][0]['suggestion']['category']=='groceries'
    assert data['rows'][0]['review_group']=='review'
    assert '同卡同日同金额' in ''.join(data['rows'][0]['suggestion']['reasons'])
    assert not data['rows'][1]['suggestion']['ready']
    assert data['rows'][1]['suggestion']['category'] is None


def test_nonpersonal_unclassified_does_not_teach_personal_category(tmp_path):
    repo=make_repo(tmp_path)
    data=batch_preview(1)
    data['rows'][0]['merchant']='UNKNOWN FAMILY MERCHANT'
    data=suggest_matches(data,repo._path,42)
    confirm_rows(repo,42,data,[{'row_key':data['rows'][0]['row_key'],'action':'import','category':'other','is_personal':False}])
    future=batch_preview(1)
    future['rows'][0].update(merchant='UNKNOWN FAMILY MERCHANT',amount='99.99',signed_amount='99.99',transaction_date='2026-11-01',posting_date='2026-11-02')
    identify_rows(future,b'future-family')
    result=suggest_matches(future,repo._path,42)['rows'][0]
    assert result['suggestion']['category'] is None
    assert result['review_group']=='review'


def test_confirm_300_rows_atomically_and_reject_repeated_batch(tmp_path):
    repo=make_repo(tmp_path)
    data=suggest_matches(batch_preview(300),repo._path,42)
    assert data['preprocessing']['counts']['ready_import']==300
    result=confirm_rows(repo,42,data,bodies(data))
    assert result['count']==300 and repo.count_transactions()==300
    assert all(not item['is_personal'] for item in result['results'])
    with pytest.raises(RecordConflictError):
        confirm_rows(repo,42,data,bodies(data))
    assert repo.count_transactions()==300


def test_invalid_last_item_rolls_back_entire_batch_including_drafts_and_links(tmp_path):
    repo=make_repo(tmp_path)
    data=suggest_matches(batch_preview(20),repo._path,42)
    items=bodies(data);items[-1]['category']='invalid-category'
    with pytest.raises(ValidationError):
        confirm_rows(repo,42,data,items)
    with sqlite3.connect(repo._path) as conn:
        for table in ['transactions','drafts','statement_links']:
            assert conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    with pytest.raises(ValueError):
        confirm_rows(repo,42,data,[items[0],items[0]])


def test_history_overrides_rule_without_leaking_owner_or_inferring_personal_flag(tmp_path):
    repo=make_repo(tmp_path)
    data=suggest_matches(batch_preview(1),repo._path,42)
    item=bodies(data)[0];item['category']='entertainment'
    confirm_rows(repo,42,data,[item])
    future=batch_preview(1)
    future['rows'][0].update(amount='123.45',signed_amount='123.45',transaction_date='2026-11-01',posting_date='2026-11-03')
    identify_rows(future,b'future-synthetic')
    own=suggest_matches(copy.deepcopy(future),repo._path,42)
    other=suggest_matches(copy.deepcopy(future),repo._path,84)
    assert own['rows'][0]['suggestion']['category']=='entertainment'
    assert own['rows'][0]['suggestion']['source']=='你已确认的商户分类'
    assert own['rows'][0]['suggestion']['is_personal'] is True
    assert other['rows'][0]['suggestion']['category']=='groceries'


def test_explicit_card_defaults_persist_and_apply_only_to_owner(tmp_path):
    repo=make_repo(tmp_path)
    data=batch_preview(1);card=data['cards'][0]['account']
    save_card_default(repo,42,data,{'card':card,'is_personal':False})
    assert suggest_matches(copy.deepcopy(data),repo._path,42)['rows'][0]['suggestion']['is_personal'] is False
    assert suggest_matches(copy.deepcopy(data),repo._path,84)['rows'][0]['suggestion']['is_personal'] is True
    with pytest.raises(ValueError):
        save_card_default(repo,42,data,{'card':'not-in-this-preview','is_personal':False})
    assert repo.count_transactions()==0


def test_exact_unique_match_suggested_but_shared_candidate_needs_review(tmp_path):
    from test_web import add
    from datetime import date
    from finance_bot.web import LedgerReader
    repo=make_repo(tmp_path)
    add(repo,amount='0.01',merchant='FAIRPRICE TEST 1')
    stored=repo.get_transaction(1)
    expense=stored.expense.model_copy(update={'transaction_date':date(2026,10,1),'payment_account':'SG UOB 4444'})
    current=LedgerReader(repo._path,42).read({'month':['']})['records'][0]
    repo.edit_record('transactions',1,42,expense,current['revision'])
    data=suggest_matches(batch_preview(1),repo._path,42)
    assert data['rows'][0]['review_group']=='ready_match'
    duplicated=batch_preview(2)
    duplicated['rows'][1].update(amount='0.01',signed_amount='0.01')
    data=suggest_matches(duplicated,repo._path,42)
    assert all(row['review_group']=='review' for row in data['rows'])


def test_batch_api_prebody_auth_csrf_saved_defaults_and_replay(monkeypatch,tmp_path):
    with running(tmp_path) as (repo,reader,client):
        assert client.post('/api/statements/confirm-batch',json={'items':[]}).status_code==401
        login(client)
        monkeypatch.setattr('finance_bot.web.preview_pdf',lambda _:batch_preview(300))
        preview=client.post('/api/statements/preview',json={'pdf_base64':base64.b64encode(b'%PDF-synthetic').decode()}).json()
        body={'preview_id':preview['preview_id'],'items':bodies(preview)}
        assert len(json.dumps(body))>32768
        assert client.post('/api/statements/confirm-batch',json=body,headers={'X-CSRF-Token':'bad'}).status_code==403
        pref=client.post('/api/statements/card-default',json={'preview_id':preview['preview_id'],
            'card':preview['cards'][0]['account'],'is_personal':False})
        assert pref.status_code==200
        result=client.post('/api/statements/confirm-batch',json=body)
        assert result.status_code==200 and result.json()['count']==300
        assert client.post('/api/statements/confirm-batch',json=body).status_code==409
        assert repo.count_transactions()==300
        assert reader.read({'month':['']})['totals']['SGD']=='0.00'


def test_conflicting_category_history_stays_in_review(tmp_path):
    from finance_bot.statement_suggestions import preprocess
    data=batch_preview(1);row=data['rows'][0]
    row.update(processed=None,candidates=[],candidate_count=0,possible_duplicate_count=0)
    links={str(i):{'statement_json':json.dumps({'merchant':row['merchant']}),
        'expense_json':json.dumps({'category':category})} for i,category in enumerate(['dining','shopping'])}
    result=preprocess(data,links,{})
    assert result['rows'][0]['review_group']=='review'
    assert result['rows'][0]['suggestion']['category'] is None


def test_failed_batch_rolls_back_matched_attribution_and_audit(tmp_path):
    from datetime import date
    from finance_bot.web import LedgerReader
    from test_web import add
    repo=make_repo(tmp_path);add(repo,amount='0.01',merchant='FAIRPRICE TEST 1')
    reader=LedgerReader(repo._path,42)
    record=reader.read({'month':['']})['records'][0]
    expense=repo.get_transaction(1).expense.model_copy(update={'transaction_date':date(2026,10,1),'payment_account':'SG UOB 4444'})
    repo.edit_record('transactions',1,42,expense,record['revision'])
    data=suggest_matches(batch_preview(2),repo._path,42)
    candidate=data['rows'][0]['candidates'][0]
    items=bodies(data)
    items[0].update(action='match',transaction_id=1,revision=candidate['revision'])
    items[1]['category']='invalid'
    with pytest.raises(ValidationError):
        confirm_rows(repo,42,data,items)
    assert repo.get_transaction(1).expense.is_personal is True
    with sqlite3.connect(repo._path) as conn:
        assert conn.execute('SELECT count(*) FROM ledger_edits').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM statement_links').fetchone()[0]==0


def test_card_default_upgrade_creates_backup_without_changing_transactions(tmp_path):
    from test_web import add
    repo=make_repo(tmp_path);add(repo)
    with sqlite3.connect(repo._path) as conn:
        conn.execute('DROP TABLE statement_card_defaults')  # Simulate only the older schema in this temporary test database.
    prepare_links(repo);prepare_links(repo)
    backups=list((tmp_path/'backups').glob('*before-statement-batch-*.db'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute('SELECT count(*) FROM transactions').fetchone()[0]==1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_card_defaults'").fetchone()
    assert repo.count_transactions()==1
