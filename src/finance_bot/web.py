# 로컬 전용 읽기 전용 웹 장부와 정확한 통화별 집계를 제공합니다.

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ExpenseExtraction


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
            for table, status in (("transactions", "confirmed"), ("drafts", "pending")):
                sql = f"SELECT * FROM {table} WHERE telegram_user_id = ?"
                if status == "pending":
                    sql += " AND status = 'pending'"
                for row in conn.execute(sql, (self.owner,)):
                    expense = ExpenseExtraction.model_validate_json(row["expense_json"])
                    rows.append({
                        "id": row["id"], "state": status,
                        "source_text": row["source_text"], "created_at": row["created_at"],
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
            if row["category"] not in {"transfer", "cash_withdrawal"}:
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


def make_handler(reader: LedgerReader) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            # Reject cross-site requests and untrusted Host headers (including DNS rebinding).
            try:
                host = urlsplit("//" + self.headers.get("Host", "")).hostname
            except ValueError:
                host = None
            if host not in {"127.0.0.1", "localhost"} or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.respond(403, b"Local access only", "text/plain")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/ledger":
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

        def respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
    parser = argparse.ArgumentParser(description="Private local read-only finance ledger")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--user-id", type=int, default=None)
    args = parser.parse_args()
    try:
        settings = WebSettings()
        reader = LedgerReader(settings.database_path, settings.owner(args.user_id), settings.app_timezone)
        if not reader.path.is_file():
            parser.error("Ledger not found. Run from the project folder after logging an expense.")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(reader))
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
