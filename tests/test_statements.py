# 합성 UOB 명세서로 파싱과 업로드 보안 및 읽기 전용 대조 후보를 검증합니다.
import base64
import copy
import json
import sqlite3
import subprocess
from contextlib import closing
from http.client import HTTPConnection

import pytest

from finance_bot.statements import parse_uob_text, preview_pdf, suggest_matches, MAX_UPLOAD_BYTES
from finance_bot import statements
from test_web_editing import running, login
from test_web import add


SAMPLE = '''United Overseas Bank Limited
Statement Date 12 OCT 2026
UOB PRVI MILES VISA CARD
1111-2222-3333-4444 SYNTHETIC PERSON
Post Trans Description of Transaction Transaction Amount
Date Date SGD
PREVIOUS BALANCE 100.00
02 OCT 02 OCT PAYMT THRU E-BANK/HOMEB/CYBERB (EP00) 100.00CR
03 OCT 01 OCT KOPITIAM TEST 12.50
Ref No. : 12345678901234567890123
TWD 300.00
04 OCT 02 OCT TEST REFUND 2.50 CR
SUB TOTAL 10.00
TOTAL BALANCE FOR UOB PRVI MILES VISA CARD 10.00
End of Transaction Details
'''


def pdf_bytes(text=SAMPLE):
    # Minimal synthetic ASCII PDF; no personal statement fixture is committed.
    lines = text.splitlines()
    stream = 'BT /F1 10 Tf 40 760 Td 14 TL\n' + '\n'.join(
        '(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj T*'
        for line in lines) + '\nET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream'.encode()]
    output, offsets = b'%PDF-1.4\n', [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(output)
    output += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{pos:010d} 00000 n \n'.encode() for pos in offsets[1:])
    return output + f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


def ledger_snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        return list(conn.iterdump())


def test_parser_payment_refund_foreign_dates_balance_and_privacy():
    data = parse_uob_text([SAMPLE])
    assert len(data['rows']) == 3
    assert [r['kind'] for r in data['rows']] == ['payment', 'charge', 'credit']
    assert data['rows'][0]['signed_amount'] == '-100.00'
    assert data['rows'][1]['original_currency'] == 'TWD'
    assert data['rows'][1]['amount'] == '12.50'
    assert data['rows'][1]['transaction_date'] == '2026-10-01'
    assert data['rows'][1]['posting_date'] == '2026-10-03'
    assert data['cards'][0]['balance_ok'] is True
    assert data['warnings'] == []
    encoded = json.dumps(data)
    assert '1111-2222' not in encoded and 'SYNTHETIC PERSON' not in encoded
    assert '12345678901234567890123' not in encoded


def test_multiple_cards_continuation_year_rollover_and_credit_balance():
    page1 = SAMPLE.split('SUB TOTAL')[0].replace('12 OCT 2026', '12 JAN 2026').replace('OCT', 'DEC')
    page2 = '''UOB PRVI MILES VISA CARD
1111-2222-3333-4444 SYNTHETIC PERSON (continued)
SUB TOTAL 10.00
PREFERRED VISA
9999-8888-7777-6666 SYNTHETIC PERSON
Date Date SGD
PREVIOUS BALANCE 5.00CR
03 JAN 01 JAN NEW PURCHASE 2.00
SUB TOTAL 3.00CR
End of Transaction Details'''
    data = parse_uob_text([page1, page2])
    assert len(data['cards']) == 2
    assert all(card['balance_ok'] for card in data['cards'])
    assert data['rows'][1]['transaction_date'] == '2025-12-01'
    assert data['rows'][-1]['transaction_date'] == '2026-01-01'
    assert data['rows'][-1]['page'] == 2


def test_unparsed_rows_and_bad_or_missing_balances_are_not_silently_accepted():
    data = parse_uob_text([SAMPLE.replace('12.50', 'unreadable')])
    assert data['warnings'] and not data['cards'][0]['balance_ok']
    data = parse_uob_text([SAMPLE.replace('SUB TOTAL 10.00', '')])
    assert not data['cards'][0]['balance_ok']
    assert data['cards'][0]['difference'] is None


@pytest.mark.parametrize('content', ['', 'a scanned image', SAMPLE.replace('Date Date SGD', 'Date Date MYR')])
def test_rejects_unsupported_text(content):
    with pytest.raises(ValueError):
        parse_uob_text([content])


def test_real_pdf_worker_and_invalid_pdf():
    assert preview_pdf(pdf_bytes())['cards'][0]['balance_ok']
    with pytest.raises(ValueError):
        preview_pdf(b'not a pdf')
    with pytest.raises(ValueError):
        preview_pdf(b'%PDF-corrupt')
    with pytest.raises(ValueError):
        preview_pdf(b'%PDF-' + b'x' * (6 * 1024 * 1024))


def test_timeout_and_busy_workers_fail_closed_and_release_slots(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('synthetic-worker', 20)
    monkeypatch.setattr(statements.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='20'):
        preview_pdf(b'%PDF-test')
    assert statements._workers.acquire(blocking=False)
    assert statements._workers.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match='正在处理'):
            preview_pdf(b'%PDF-test')
    finally:
        statements._workers.release()
        statements._workers.release()


def test_matching_is_exact_owner_scoped_conservative_and_read_only(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        # Unknown account is eligible but clearly marked for card verification.
        add(repo)
        with sqlite3.connect(reader.path) as conn:
            raw = json.loads(conn.execute('SELECT expense_json FROM transactions').fetchone()[0])
            raw.update(amount='12.50', transaction_date='2026-10-01', payment_account='SG UOB 4444', merchant='午餐')
            conn.execute('UPDATE transactions SET expense_json=?', (json.dumps(raw),))
            for account, owner in [('SG DBS',42),('MY UOB',42),('SG UOB 5555',42),('SG UOB 4444',84),('',42)]:
                other = copy.deepcopy(raw)
                other['payment_account'] = account
                conn.execute('INSERT INTO transactions(draft_id,telegram_user_id,source_text,expense_json,created_at) VALUES(?,?,?,?,?)',
                    (100 + conn.execute('SELECT count(*) FROM transactions').fetchone()[0], owner, 'synthetic', json.dumps(other), '2026-10-01'))
        before = ledger_snapshot(reader.path)
        data = suggest_matches(parse_uob_text([SAMPLE]), reader.path, 42)
        assert data['rows'][0]['candidates'] == []
        assert data['rows'][2]['candidates'] == []
        assert data['rows'][1]['candidate_count'] == 2
        assert data['rows'][1]['candidates'][0]['merchant'] == '午餐'
        assert ledger_snapshot(reader.path) == before


def test_upload_requires_login_csrf_origin_and_never_writes_ledger(tmp_path):
    with running(tmp_path) as (repo, reader, client):
        url = '/api/statements/preview'
        body = {'pdf_base64': base64.b64encode(pdf_bytes()).decode()}
        assert client.post(url, json=body).status_code == 401
        login(client)
        before = ledger_snapshot(reader.path)
        assert client.post(url, json=body, headers={'X-CSRF-Token':'wrong'}).status_code == 403
        assert client.post(url, json=body, headers={'Origin':'https://evil.example'}).status_code == 403
        response = client.post(url, json=body)
        assert response.status_code == 200
        assert response.json()['preview_only'] is True
        assert response.headers['Cache-Control'] == 'no-store'
        assert ledger_snapshot(reader.path) == before
        assert repo.count_transactions() == 0
        assert client.post(url, json={'pdf_base64':'%%%'}).status_code == 422
        assert client.post(url, json={'pdf_base64':42}).status_code == 422
        connection = HTTPConnection('127.0.0.1', client.base_url.port)
        connection.putrequest('POST', url)
        for name, value in {'Origin': str(client.base_url).rstrip('/'), 'Content-Type':'application/json',
                'Cookie':'finance_session=' + client.cookies.get('finance_session'),
                'X-CSRF-Token':client.headers['X-CSRF-Token'], 'Content-Length':str(MAX_UPLOAD_BYTES+1)}.items():
            connection.putheader(name, value)
        connection.endheaders()
        assert connection.getresponse().status == 400
        connection.close()
