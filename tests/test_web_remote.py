# Tailscale 전용 원본 주소와 사용자 및 보안 쿠키 검증을 테스트합니다.
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from finance_bot.web import LedgerReader, make_handler
from finance_bot.web_auth import WebAuth


def test_private_proxy_requires_exact_host_identity_and_https_origin(tmp_path):
    origin = 'https://test-device.example.ts.net'
    auth = WebAuth(tmp_path / 'auth.json')
    auth.login('synthetic-test-password', setup=True)
    reader = LedgerReader(tmp_path / 'not-needed.db', 42)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(reader, auth, public_origin=origin, tailscale_login='owner@example.com'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            assert client.get('/api/session').status_code == 200
            remote = {'Host':'test-device.example.ts.net', 'Tailscale-User-Login':'owner@example.com'}
            assert client.get('/api/session', headers={'Host':'test-device.example.ts.net'}).status_code == 403
            assert client.get('/api/session', headers={**remote,'Tailscale-User-Login':'other@example.com'}).status_code == 403
            assert client.get('/api/session', headers={**remote,'Host':'other.example.ts.net'}).status_code == 403
            assert client.get('/api/session', headers=remote).status_code == 200
            assert client.post('/api/login', headers={**remote,'Origin':'http://test-device.example.ts.net'}, json={'password':'synthetic-test-password'}).status_code == 403
            result = client.post('/api/login', headers={**remote,'Origin':origin}, json={'password':'synthetic-test-password'})
            assert result.status_code == 200
            assert '; Secure' in result.headers['set-cookie']
            token = result.cookies.get('finance_session')
            session_headers = {**remote, 'Origin':origin, 'Cookie':f'finance_session={token}'}
            assert client.get('/api/session', headers=session_headers).json()['authenticated'] is True
            assert client.post('/api/logout', headers=session_headers, json={}).status_code == 403
            result2 = client.post('/api/logout', headers={**session_headers,'X-CSRF-Token':result.json()['csrf']}, json={})
            assert result2.status_code == 200
            assert '; Secure' in result2.headers['set-cookie']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('origin,login',[
    ('http://test.example.ts.net','owner@example.com'),
    ('https://evil.example','owner@example.com'),
    ('https://test.example.ts.net/path','owner@example.com'),
    ('https://test.example.ts.net',''),
])
def test_invalid_remote_config_fails_closed(tmp_path, origin, login):
    with pytest.raises(ValueError):
        make_handler(LedgerReader(tmp_path/'ledger.db',42), public_origin=origin,tailscale_login=login)
