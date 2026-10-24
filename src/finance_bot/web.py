# 로컬 전용 읽기 전용 웹 장부와 정확한 통화별 집계를 제공합니다.

import argparse
import base64
import binascii
import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import hmac
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError

from .models import ExpenseExtraction
from .database import FinanceRepository, DraftNotPendingError, RecordConflictError, record_revision
from .web_auth import WebAuth, LoginError
from .statements import MAX_UPLOAD_BYTES, preview_pdf, suggest_matches
from .reconciliation import PreviewStore, prepare_links, identify_rows, confirm_row


ASSETS = Path(__file__).parent / "static"
CATEGORIES = {
    "dining": "餐饮", "groceries": "日用品与买菜", "transport": "交通",
    "shopping": "购物", "bills": "账单", "housing": "住房", "health": "健康",
    "education": "教育", "travel": "旅行", "entertainment": "娱乐",
    "cash_withdrawal": "取现", "transfer": "转账", "other": "其他",
}


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_path: Path = Path("data/finance.db")
    telegram_allowed_user_ids: str
    app_timezone: str = "Asia/Singapore"
    web_public_origin: str = ""
    web_tailscale_login: str = ""

    def remote_access(self) -> tuple[str, str]:
        config_path = self.database_path.with_name(self.database_path.name + '.remote.json')
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        return (self.web_public_origin or config.get('origin', ''),
                self.web_tailscale_login or config.get('tailscale_login', ''))

    def owner(self, requested: int | None) -> int:
        try:
            ids = {int(item.strip()) for item in self.telegram_allowed_user_ids.split(",")}
        except ValueError as exc:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain numeric IDs.") from exc
        if not ids or any(value <= 0 for value in ids):
            raise ValueError("Set TELEGRAM_ALLOWED_USER_IDS to your real Telegram user ID first.")
        if requested is not None and requested in ids:
            return requested
        if requested is not None or len(ids) != 1:
            raise ValueError("Choose one allowlisted owner with --user-id; ledgers are never combined.")
        return next(iter(ids))


class LedgerReader:
    def __init__(self, path: Path, owner: int, timezone: str = "Asia/Singapore") -> None:
        self.path = path.resolve()
        self.owner = owner
        self.timezone = ZoneInfo(timezone)

    def read(self, params: dict[str, list[str]]) -> dict:
        def value(name: str, default: str = "") -> str:
            return params.get(name, [default])[0].strip()

        month = value("month", datetime.now(self.timezone).strftime("%Y-%m"))
        if month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("Invalid month")
        state = value("state", "confirmed")
        if state not in {"confirmed", "pending"}:
            raise ValueError("Invalid state")
        page = int(value("page", "1"))
        if page < 1 or page > 1_000_000:
            raise ValueError("Invalid page")
        query, account, currency, category = (value(k) for k in ("q", "account", "currency", "category"))
        if len(query) > 200:
            raise ValueError("Search is too long")

        rows = []
        # Read-only connection and a shared snapshot: this page cannot alter the bot's ledger.
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            links = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_links'").fetchone():
                links = {r['transaction_id']: {**json.loads(r['statement_json']), 'statement_date': r['statement_date']}
                    for r in conn.execute('SELECT * FROM statement_links WHERE telegram_user_id=?', (self.owner,))}
            for table, status in (("transactions", "confirmed"), ("drafts", "pending")):
                sql = f"SELECT * FROM {table} WHERE telegram_user_id = ?"
                if status == "pending":
                    sql += " AND status = 'pending'"
                for row in conn.execute(sql, (self.owner,)):
                    expense = ExpenseExtraction.model_validate_json(row["expense_json"])
                    rows.append({
                        "id": row["id"], "state": status,
                        "revision": record_revision(row),
                        "source_text": row["source_text"], "created_at": row["created_at"],
                        "statement": links.get(row['id']) if status == 'confirmed' else None,
                        **expense.model_dump(mode="json"),
                    })

        options = {
            "months": sorted({r["transaction_date"][:7] for r in rows}, reverse=True),
            "accounts": sorted({r["payment_account"] for r in rows if r["payment_account"]}),
            "currencies": sorted({"SGD", "MYR"} | {r["currency"] for r in rows}),
            "categories": CATEGORIES,
        }

        def matches(row: dict) -> bool:
            searchable = " ".join(str(row.get(k) or "") for k in ("merchant", "note", "source_text"))
            return (
                (not month or row["transaction_date"].startswith(month))
                and (not account or (row["payment_account"] or "__unknown__") == account)
                and (not currency or row["currency"] == currency)
                and (not category or row["category"] == category)
                and (not query or query.casefold() in searchable.casefold())
            )

        filtered = [row for row in rows if matches(row)]
        totals = defaultdict(Decimal)
        groups = defaultdict(Decimal)
        confirmed = [row for row in filtered if row["state"] == "confirmed"]
        for row in confirmed:
            if row['is_personal'] and row["category"] not in {"transfer", "cash_withdrawal"}:
                amount = Decimal(row["amount"])
                totals[row["currency"]] += amount
                groups[(row["currency"], row["category"])] += amount
        selected = sorted(
            [row for row in filtered if row["state"] == state],
            key=lambda row: (row["transaction_date"], row["id"]), reverse=True,
        )
        page_size = 25
        pages = max(1, (len(selected) + page_size - 1) // page_size)
        page = min(page, pages)
        return {
            "month": month, "options": options,
            "totals": {key: format(totals[key], ".2f") for key in options["currencies"]},
            "categories": [
                {"currency": curr, "category": cat, "amount": format(amount, ".2f")}
                for (curr, cat), amount in sorted(groups.items(), key=lambda item: item[1], reverse=True)
            ],
            "confirmed_count": len(confirmed),
            "pending_count": sum(row["state"] == "pending" for row in filtered),
            "records": selected[(page - 1) * page_size:page * page_size],
            "total": len(selected), "page": page, "pages": pages,
            "updated_at": datetime.now(self.timezone).isoformat(timespec="seconds"),
        }


def make_handler(reader: LedgerReader, auth: WebAuth | None = None, *, public_origin: str = '', tailscale_login: str = '') -> type[BaseHTTPRequestHandler]:
    public_origin = public_origin.rstrip('/')
    remote = urlsplit(public_origin)
    if public_origin or tailscale_login:
        if (remote.scheme != 'https' or not remote.hostname or not remote.hostname.endswith('.ts.net')
                or remote.username or remote.password or remote.path or remote.query or remote.fragment
                or remote.port not in {None, 443} or not tailscale_login):
            raise ValueError('Remote access requires one exact Tailscale HTTPS origin and account login.')
    auth = auth or WebAuth(reader.path.with_name(reader.path.name + ".web-auth.json"))
    repository = FinanceRepository(reader.path)
    prepare_links(repository)
    previews = PreviewStore()

    class Handler(BaseHTTPRequestHandler):
        def local_request(self) -> bool:
            # Reject cross-site requests and untrusted Host headers (including DNS rebinding).
            try:
                host = self.headers.get("Host", "")
            except ValueError:
                host = None
            self.remote_request = bool(public_origin and host == remote.netloc)
            local_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if (host not in local_hosts and not self.remote_request) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.json_response(403, {"error": "访问地址或请求来源未获准。"})
                return False
            if self.remote_request and (
                self.client_address[0] != '127.0.0.1'
                or self.headers.get('Tailscale-User-Login', '').casefold() != tailscale_login.casefold()
            ):
                self.json_response(403, {"error": "只允许你的 Tailscale 账号访问。"})
                return False
            if self.remote_request and auth.needs_setup:
                self.json_response(403, {"error": "请先在 MBP 本机设置账本密码。"})
                return False
            return True

        def session_token(self) -> str:
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookies.get("finance_session")
                return value.value if value else ""
            except CookieError:
                return ""

        def do_GET(self) -> None:
            if not self.local_request():
                return
            parsed = urlsplit(self.path)
            csrf = auth.session(self.session_token())
            if parsed.path == "/api/session":
                self.json_response(200, {"authenticated": csrf is not None, "needs_setup": auth.needs_setup, "csrf": csrf})
                return
            if parsed.path == "/api/ledger":
                if csrf is None:
                    self.json_response(401, {"error": "请先登录账本。"})
                    return
                try:
                    result = reader.read(parse_qs(parsed.query, keep_blank_values=True))
                    self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")
                except (ValueError, TypeError):
                    self.respond(400, b'{"error":"Invalid filters"}', "application/json")
                except (sqlite3.Error, OSError):
                    self.respond(503, '{"error":"暂时无法读取账本，请检查数据库路径或稍后刷新。"}'.encode(), "application/json")
                return
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if parsed.path not in files:
                self.respond(404, b"Not found", "text/plain")
                return
            filename, content_type = files[parsed.path]
            self.respond(200, (ASSETS / filename).read_bytes(), content_type)

        def do_POST(self) -> None:
            if not self.local_request():
                return
            expected_origin = public_origin if self.remote_request else "http://" + self.headers.get("Host", "")
            if self.headers.get("Origin") != expected_origin:
                self.json_response(403, {"error": "请求来源不正确，请重新打开页面。"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.json_response(415, {"error": "需要 JSON 请求。"})
                return
            path = urlsplit(self.path).path
            statement_upload = path == '/api/statements/preview'
            if statement_upload:
                csrf = auth.session(self.session_token())
                if csrf is None:
                    self.json_response(401, {"error": "请先登录账本。"})
                    return
                if not hmac.compare_digest(csrf, self.headers.get('X-CSRF-Token', '')):
                    self.json_response(403, {"error": "安全凭证已失效，请刷新页面。"})
                    return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (MAX_UPLOAD_BYTES if statement_upload else 32768):
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.json_response(400, {"error": "请求内容无效。"})
                return
            if path in {"/api/setup", "/api/login"}:
                try:
                    token, csrf = auth.login(body.get("password"), setup=path == "/api/setup")
                    self.json_response(200, {"csrf": csrf}, cookie=f"finance_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
                except LoginError as exc:
                    self.json_response(400, {"error": str(exc)})
                except (OSError, ValueError, KeyError):
                    self.json_response(503, {"error": "无法读取或保存页面登录设置。"})
                return
            token = self.session_token()
            csrf = auth.session(token)
            if csrf is None:
                self.json_response(401, {"error": "登录已过期，请重新登录。"})
                return
            if not hmac.compare_digest(csrf, self.headers.get("X-CSRF-Token", "")):
                self.json_response(403, {"error": "安全凭证已失效，请刷新页面。"})
                return
            if path == "/api/logout":
                auth.logout(token)
                self.json_response(200, {"ok": True}, cookie="finance_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                return
            if path == '/api/statements/confirm':
                try:
                    preview = previews.get(body.get('preview_id'), token)
                    result = confirm_row(repository, reader.owner, preview, body)
                    updated_preview = suggest_matches(preview, reader.path, reader.owner)
                    updated_preview['preview_id'] = previews.put(token, updated_preview)
                    result['preview'] = updated_preview
                    self.json_response(200, result)
                except ValidationError:
                    self.json_response(422, {'error':'请选择有效的消费分类。'})
                except (RecordConflictError, DraftNotPendingError) as exc:
                    self.json_response(409, {'error':str(exc) if not str(exc).startswith(('Record', 'Draft')) else '记录已经更新，请重新上传后核对。'})
                except (ValueError, TypeError) as exc:
                    self.json_response(400, {'error':str(exc) or '请检查确认内容。'})
                except sqlite3.IntegrityError:
                    self.json_response(409, {'error':'这笔交易或明细已关联，不能重复处理。'})
                except sqlite3.Error:
                    self.json_response(503, {'error':'暂时无法保存，请稍后再试。'})
                return
            if statement_upload:
                try:
                    encoded = body.get('pdf_base64')
                    if not isinstance(encoded, str):
                        raise ValueError('请选择 PDF 文件。')
                    data = base64.b64decode(encoded, validate=True)
                    preview = preview_pdf(data)
                    identify_rows(preview, data)
                    preview = suggest_matches(preview, reader.path, reader.owner)
                    preview['preview_id'] = previews.put(token, preview)
                    self.json_response(200, preview)
                except (binascii.Error, ValueError):
                    self.json_response(422, {"error": "未能识别支持的账单格式。当前支持新加坡 UOB 信用卡电子账单；请使用 6 MB 内、1–20 页的未加密原始 PDF。其他银行、扫描件及银行账户流水暂不支持。"})
                except (sqlite3.Error, OSError, subprocess.SubprocessError):
                    self.json_response(503, {"error": "暂时无法处理账单，请稍后重试。"})
                return
            match = re.fullmatch(r"/api/(transactions|drafts)/([1-9]\d*)/(edit|confirm|cancel)", path)
            if not match:
                self.json_response(404, {"error": "操作不存在。"})
                return
            table, raw_id, action = match.groups()
            revision = body.get("revision", "")
            if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
                self.json_response(400, {"error": "记录版本无效，请刷新后重试。"})
                return
            try:
                if action == "edit":
                    raw = body.get("expense")
                    if not isinstance(raw, dict):
                        raise ValueError()
                    expense = ExpenseExtraction.model_validate({
                        **raw, "confidence": 1, "needs_review": False, "ambiguities": [],
                    })
                    new_revision = repository.edit_record(table, int(raw_id), reader.owner, expense, revision)
                    self.json_response(200, {"ok": True, "revision": new_revision})
                elif table == "drafts":
                    if action == "confirm":
                        repository.confirm_draft(int(raw_id), reader.owner, revision)
                    else:
                        repository.cancel_draft(int(raw_id), reader.owner, revision)
                    self.json_response(200, {"ok": True})
                else:
                    self.json_response(400, {"error": "已确认交易不能再次审核。"})
            except ValidationError as exc:
                fields = {str(error["loc"][0]): error["msg"] for error in exc.errors(include_input=False)}
                self.json_response(422, {"error": "请检查标记的字段：金额需大于零且最多两位小数，其他字段需完整。", "fields": fields})
            except (DraftNotPendingError, RecordConflictError):
                self.json_response(409, {"error": "记录已更新或已关联账单。已关联的金额、日期、币种与账户不可改动；分类和个人支出仍可修改。请关闭并刷新后重试。"})
            except (ValueError, TypeError):
                self.json_response(400, {"error": "请填写完整的交易资料。"})
            except sqlite3.Error:
                self.json_response(503, {"error": "暂时无法保存，请稍后重试。"})

        def json_response(self, status: int, value: dict, cookie: str | None = None) -> None:
            self.respond(status, json.dumps(value, ensure_ascii=False).encode(), "application/json", cookie)

        def respond(self, status: int, body: bytes, content_type: str, cookie: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                if getattr(self, 'remote_request', False):
                    cookie += '; Secure'
                self.send_header("Set-Cookie", cookie)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log search terms, transaction contents or request URLs.
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Private local finance ledger")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user-id", type=int, default=None)
    args = parser.parse_args()
    try:
        settings = WebSettings()
        reader = LedgerReader(settings.database_path, settings.owner(args.user_id), settings.app_timezone)
        if not reader.path.is_file():
            parser.error("Ledger not found. Run from the project folder after logging an expense.")
        FinanceRepository(reader.path).prepare_web_edits()
        origin, login = settings.remote_access()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(reader, public_origin=origin, tailscale_login=login))
    except (ValueError, OSError):
        parser.error("Check DATABASE_PATH, TELEGRAM_ALLOWED_USER_IDS, APP_TIMEZONE and port. For multiple owners, use --user-id.")
    print(f"账本已启动：http://127.0.0.1:{server.server_port} （Control+C 停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
