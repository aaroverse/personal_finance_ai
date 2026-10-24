# 웹 장부의 통화별 집계와 사용자 격리 및 읽기 전용 경계를 검증합니다.

import threading
from datetime import date
from http.server import ThreadingHTTPServer

import httpx
import pytest

from finance_bot.database import FinanceRepository
from finance_bot.models import ExpenseExtraction
from finance_bot.web import LedgerReader, WebSettings, make_handler


def add(repo, owner=42, amount="0.10", currency="SGD", category="dining", merchant="午餐", pending=False):
    expense = ExpenseExtraction(
        transaction_date=date(2026, 10, 22), amount=amount, currency=currency,
        merchant=merchant, category=category, payment_account="SG UOB 信用卡",
        confidence=0.9, needs_review=False, ambiguities=[],
    )
    draft = repo.create_draft(owner, "原始描述 " + merchant, expense)
    if not pending:
        repo.confirm_draft(draft.id, owner)
    return draft


@pytest.fixture
def ledger(tmp_path):
    repo = FinanceRepository(tmp_path / "finance.db")
    repo.initialize()
    repo.prepare_web_edits()
    return repo, LedgerReader(tmp_path / "finance.db", 42)


def test_totals_are_exact_separate_and_exclude_pending_and_transfers(ledger):
    repo, reader = ledger
    add(repo, amount="0.10")
    add(repo, amount="0.20")
    add(repo, amount="14.50", currency="MYR")
    add(repo, amount="999.00", category="transfer")
    add(repo, amount="500.00", pending=True)
    add(repo, owner=84, amount="800.00")
    data = reader.read({"month": [""]})
    assert data["totals"] == {"MYR": "14.50", "SGD": "0.30"}
    assert data["confirmed_count"] == 4
    assert data["pending_count"] == 1
    assert len(data["records"]) == 4


def test_filters_and_pagination_apply_to_full_ledger(ledger):
    repo, reader = ledger
    for _ in range(27):
        add(repo)
    data = reader.read({"month": ["2026-10"], "page": ["2"], "q": ["午餐"]})
    assert data["total"] == 27
    assert len(data["records"]) == 2
    assert data["totals"]["SGD"] == "2.70"
    assert reader.read({"month": ["2026-09"]})["total"] == 0
    assert reader.read({"month": [""], "account": ["wrong"]})["total"] == 0
    assert reader.read({"month": [""], "currency": ["MYR"]})["total"] == 0
    assert reader.read({"month": [""], "category": ["shopping"]})["total"] == 0


def test_pending_and_cancelled_are_not_confirmed(ledger):
    repo, reader = ledger
    draft = add(repo, pending=True)
    data = reader.read({"month": [""], "state": ["pending"]})
    assert data["total"] == 1
    assert data["records"][0]["state"] == "pending"
    repo.cancel_draft(draft.id, 42)
    assert reader.read({"month": [""], "state": ["pending"]})["total"] == 0


def test_reader_does_not_create_missing_database(tmp_path):
    reader = LedgerReader(tmp_path / "missing.db", 42)
    import sqlite3
    with pytest.raises(sqlite3.Error):
        reader.read({})
    assert not reader.path.exists()


def test_multi_owner_requires_explicit_allowlisted_owner():
    settings = WebSettings(_env_file=None, telegram_allowed_user_ids="42,84")
    with pytest.raises(ValueError):
        settings.owner(None)
    with pytest.raises(ValueError):
        settings.owner(99)
    assert settings.owner(42) == 42


@pytest.mark.parametrize("params", [{"page": ["0"]}, {"month": ["2026-99"]}, {"state": ["cancelled"]}])
def test_rejects_invalid_filters(ledger, params):
    with pytest.raises(ValueError):
        ledger[1].read(params)


def test_http_does_not_serve_files_or_allow_cross_site_access(ledger):
    repo, reader = ledger
    add(repo, merchant="<script>alert('test')</script>")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(reader))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get('/api/ledger').status_code == 401
            client.headers['Origin'] = f"http://127.0.0.1:{server.server_port}"
            assert client.post('/api/setup', json={'password': 'test-password-1234'}).status_code == 200
            response = client.get("/api/ledger?month=")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert response.json()["records"][0]["merchant"].startswith("<script>")
            assert client.get("/.env").status_code == 404
            assert client.get("/data/finance.db").status_code == 404
            assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
            assert client.get("/api/ledger", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
            assert client.post("/api/ledger", json={}).status_code == 403
            assert "textContent" in client.get("/app.js").text
            assert "innerHTML" not in client.get("/app.js").text
            flow = client.get("/statement-flow.js")
            assert flow.status_code == 200
            assert "StatementFlow" in flow.text
        assert repo.count_transactions() == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
