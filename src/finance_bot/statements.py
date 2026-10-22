# UOB 싱가포르 카드 명세서를 로컬에서 미리 보고 보수적인 대조 후보를 찾습니다.
import io
import json
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

MAX_PDF_BYTES = 6 * 1024 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 + 1024
_workers = threading.BoundedSemaphore(2)
_months = {name: index for index, name in enumerate(
    'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split(), 1)}
_money = r'\d[\d,]*\.\d{2}'
_dated = re.compile(r'^(\d{2} [A-Z]{3})\s+(\d{2} [A-Z]{3})\s+(.+?)\s+(' + _money + r')(?:\s*(CR))?$')
_card_number = re.compile(r'^(?:\d{4}[- ]){3}(\d{4})')


def _amount(value: str) -> Decimal:
    return Decimal(value.replace(',', ''))


def _date_before(value: str, limit: date) -> date:
    day, month = value.split()
    for year in (limit.year, limit.year - 1):
        try:
            result = date(year, _months[month], int(day))
        except (ValueError, KeyError):
            continue
        if result <= limit:
            return result
    raise ValueError('无法确认交易年份，请人工检查账单。')


def parse_uob_text(pages: list[str]) -> dict:
    text = '\n'.join(pages)
    statement_date = re.search(r'Statement Date\s+(\d{2})\s+([A-Z]{3})\s+(\d{4})', text)
    if not statement_date or 'United Overseas Bank' not in text or not re.search(r'Date\s+Date\s+SGD', text):
        raise ValueError('暂只支持新加坡 UOB 英文信用卡电子账单（SGD 结算），不是银行账户流水。')
    day, month, year = statement_date.groups()
    try:
        issued = date(int(year), _months[month], int(day))
    except (ValueError, KeyError) as exc:
        raise ValueError('账单日期无法识别。') from exc
    cards, rows, warnings = {}, [], []
    current = None
    previous_line = ''
    last_row = None
    ended = False
    for page_number, page in enumerate(pages, 1):
        if ended:
            break
        for raw in page.splitlines():
            line = ' '.join(raw.split())
            if not line:
                continue
            if 'End of Transaction Details' in line:
                ended = True
                break
            number = _card_number.match(line)
            if number and ('VISA' in previous_line or 'CARD' in previous_line):
                name = previous_line.removesuffix(' (continued)')
                key = name + ' • ' + number[1]
                current = cards.setdefault(key, {'name': name, 'last_four': number[1],
                    'account': 'SG UOB ' + key, 'opening_balance': None, 'closing_balance': None})
                last_row = None
            elif current:
                balance = re.fullmatch(r'(PREVIOUS BALANCE|SUB TOTAL)\s+(' + _money + r')(?:\s*(CR))?', line)
                match = _dated.match(line)
                if balance:
                    value = _amount(balance[2]) * (-1 if balance[3] else 1)
                    field = 'opening_balance' if balance[1] == 'PREVIOUS BALANCE' else 'closing_balance'
                    if current[field] is not None and current[field] != format(value, '.2f'):
                        warnings.append('同一张卡出现不一致的余额，请人工检查。')
                    current[field] = format(value, '.2f')
                    last_row = None
                elif match:
                    posted = _date_before(match[1], issued)
                    transacted = _date_before(match[2], posted)
                    amount = _amount(match[4])
                    credit = bool(match[5])
                    merchant = match[3]
                    kind = 'payment' if merchant.startswith('PAYMT THRU ') else ('credit' if credit else 'charge')
                    if kind == 'payment' and not credit:
                        warnings.append('还款行没有 CR 标记，请人工检查。')
                    last_row = {'id': len(rows) + 1, 'card': current['account'],
                        'posting_date': posted.isoformat(), 'transaction_date': transacted.isoformat(),
                        'merchant': merchant, 'amount': format(amount, '.2f'), 'currency': 'SGD',
                        'kind': kind, 'signed_amount': format(-amount if credit else amount, '.2f'),
                        'page': page_number}
                    rows.append(last_row)
                    if len(rows) > 1000:
                        raise ValueError('交易过多，请按月份上传。')
                elif re.match(r'^\d{2} [A-Z]{3}\s+\d{2} [A-Z]{3}', line):
                    warnings.append(f'第 {page_number} 页有交易行无法完整识别，请对照原 PDF。')
                    last_row = None
                elif last_row:
                    foreign = re.fullmatch(r'([A-Z]{3})\s+(' + _money + ')', line)
                    if foreign and foreign[1] != 'SGD':
                        last_row.update(original_currency=foreign[1], original_amount=format(_amount(foreign[2]), '.2f'))
            previous_line = line
    if not rows:
        raise ValueError('未找到可识别交易；扫描图片、加密 PDF 和其他格式暂不支持。')
    for card in cards.values():
        entries = [row for row in rows if row['card'] == card['account']]
        net = sum((Decimal(row['signed_amount']) for row in entries), Decimal(0))
        card['row_count'] = len(entries)
        card['net_movement'] = format(net, '.2f')
        opening, closing = card['opening_balance'], card['closing_balance']
        delta = Decimal(opening) + net - Decimal(closing) if opening is not None and closing is not None else None
        card['balance_ok'] = delta == 0 if delta is not None else False
        card['difference'] = format(delta, '.2f') if delta is not None else None
        if not card['balance_ok']:
            warnings.append(card['name'] + ' • ' + card['last_four'] + ' 的余额校验未通过，不可直接导入。')
    return {'bank': 'UOB SG', 'statement_date': issued.isoformat(), 'currency': 'SGD',
        'cards': list(cards.values()), 'rows': rows, 'warnings': list(dict.fromkeys(warnings)),
        'preview_only': True}


def _extract_pdf(data: bytes) -> dict:
    import pdfplumber
    if not data.startswith(b'%PDF-') or len(data) > MAX_PDF_BYTES:
        raise ValueError('请上传不超过 6 MB 的原始 PDF。')
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not 1 <= len(pdf.pages) <= 20:
                raise ValueError('请上传 1–20 页的单月账单。')
            pages = [page.extract_text() or '' for page in pdf.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('无法读取 PDF；请使用未加密的原始电子账单，不要上传扫描图片。') from exc
    return parse_uob_text(pages)


def preview_pdf(data: bytes) -> dict:
    if not data.startswith(b'%PDF-') or len(data) > MAX_PDF_BYTES:
        raise ValueError('请上传不超过 6 MB 的原始 PDF。')
    if not _workers.acquire(blocking=False):
        raise ValueError('已有账单正在处理，请稍后再试。')
    try:
        result = subprocess.run([sys.executable, '-m', 'finance_bot.statements'], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
        if result.returncode:
            raise ValueError('账单解析失败或超过处理限制，请检查文件。')
        payload = json.loads(result.stdout)
        if 'error' in payload:
            raise ValueError(payload['error'])
        return payload
    except subprocess.TimeoutExpired as exc:
        raise ValueError('解析超过 20 秒，请使用原始电子账单或更小的文件。') from exc
    finally:
        _workers.release()


def suggest_matches(preview: dict, database: Path, owner: int) -> dict:
    # Read-only, owner-scoped; candidates never modify or silently link a record.
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        records = [(rid, json.loads(expense)) for rid, expense in conn.execute(
            'SELECT id, expense_json FROM transactions WHERE telegram_user_id=?', (owner,))]
    for row in preview['rows']:
        candidates = []
        if row['kind'] == 'charge':
            for rid, expense in records:
                if expense['currency'] != 'SGD' or Decimal(expense['amount']) != Decimal(row['amount']):
                    continue
                if expense['category'] in {'transfer', 'cash_withdrawal'}:
                    continue
                days = abs((date.fromisoformat(expense['transaction_date']) - date.fromisoformat(row['transaction_date'])).days)
                account = expense.get('payment_account') or ''
                if days > 3 or (account and 'uob' not in account.casefold()):
                    continue
                digits = re.findall(r'\d{4}', account)
                if digits and digits[-1] != row['card'][-4:]:
                    continue
                if re.search(r'\b(MY|MYR|MALAYSIA)\b', account, re.I):
                    continue
                candidates.append({'id': rid, 'merchant': expense['merchant'],
                    'transaction_date': expense['transaction_date'], 'amount': expense['amount'],
                    'account': account or '未注明账户', 'day_difference': days,
                    'reason': '金额及币种一致；日期相差 ' + str(days) + ' 天；' + ('卡尾号一致' if digits else '具体卡片仍需核对')})
        candidates.sort(key=lambda item: (item['day_difference'], item['id']))
        row['candidate_count'] = len(candidates)
        row['candidates'] = candidates[:5]
    return preview


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        payload = _extract_pdf(sys.stdin.buffer.read(MAX_PDF_BYTES + 1))
    except ValueError as exc:
        payload = {'error': str(exc)}
    except Exception:
        payload = {'error': '无法识别这份 PDF，请检查文件或改用原始电子账单。'}
    print(json.dumps(payload, ensure_ascii=False))
