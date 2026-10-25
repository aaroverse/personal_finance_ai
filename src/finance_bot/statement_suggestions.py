# 사용자 확인 이력과 로컬 규칙으로 명세서 검토안을 만들고 불확실한 항목을 분리합니다.
import json
import re
from collections import Counter, defaultdict

from .models import ExpenseCategory
from typing import get_args


def merchant_key(value: str) -> str:
    return re.sub(r'\s+', ' ', value.upper()).strip()


def category_rule(merchant: str) -> str | None:
    rules = (
        (r'BUS/MRT|TAOYUAN METRO', 'transport'),
        (r'FAIRPRICE|NTUC|SHENG SIONG|SCARLETT', 'groceries'),
        (r'KOPITIAM|MCDONALD|STUFF.?D|FOUR LEAVES|RAMEN|POPEYES|MUYOO|QIN JI|XIANG XIANG|WHALE TEA', 'dining'),
        (r'HARVEY NORMAN|TAKA JEWELLERY', 'shopping'),
    )
    return next((category for pattern, category in rules if re.search(pattern, merchant_key(merchant))), None)


def preprocess(preview: dict, confirmed_links: dict, card_defaults: dict) -> dict:
    history = defaultdict(set)
    valid_categories = set(get_args(ExpenseCategory)) - {'transfer','cash_withdrawal'}
    for link in confirmed_links.values():
        bank = json.loads(link['statement_json'])
        expense = json.loads(link['expense_json'])
        if expense['category'] in valid_categories and not (expense['category']=='other' and not expense.get('is_personal', True)):
            history[merchant_key(bank['merchant'])].add(expense['category'])
    candidate_uses = Counter(c['id'] for row in preview['rows'] if not row['processed'] for c in row['candidates'])
    repeated = Counter((r['card'],r['transaction_date'],r['amount']) for r in preview['rows']
        if not r['processed'] and r['kind']=='charge')
    counts = Counter()
    for card in preview['cards']:
        card['personal_default'] = card_defaults.get(card['account'], True)
        card['personal_default_saved'] = card['account'] in card_defaults
    for row in preview['rows']:
        reasons = []
        row['suggestion'] = None
        if row['processed']:
            row['review_group'] = 'processed'
        elif row['kind'] != 'charge':
            row['review_group'] = 'ignored'
        else:
            candidates = row['candidates']
            candidate = candidates[0] if row['candidate_count']==1 else None
            category, source = None, '未知商户'
            personal = card_defaults.get(row['card'], candidate['is_personal'] if candidate else True)
            if candidate:
                category, source = candidate['category'], '已有记账分类'
                if candidate['day_difference']:
                    reasons.append('消费日期不完全一致')
                exact_card = row['card'][-4:] in re.findall(r'\d{4}', candidate['account'])
                if not exact_card and merchant_key(candidate['merchant']) != merchant_key(row['merchant']):
                    reasons.append('具体卡片或商户描述仍需核对')
                if candidate_uses[candidate['id']] > 1:
                    reasons.append('多行推荐同一笔记账')
            else:
                known = history.get(merchant_key(row['merchant']), set())
                if len(known)==1:
                    category, source = next(iter(known)), '你已确认的商户分类'
                elif len(known)>1:
                    reasons.append('此商户的历史分类不一致')
                else:
                    category = category_rule(row['merchant'])
                    source = '本机商户规则' if category else '请指定分类'
                if row['candidate_count'] > 1:
                    reasons.append('有多个匹配候选')
                elif row['possible_duplicate_count']:
                    reasons.append('存在已关联的近似消费')
            if not category:
                reasons.append('分类需要确认')
            if row.get('requires_recheck'):
                reasons.append('这笔明细曾更正，需要重新核对')
            if row['amount']=='0.00':
                reasons.append('零金额需人工检查，不能补录为消费')
            if repeated[(row['card'],row['transaction_date'],row['amount'])] > 1:
                reasons.append('账单内有同卡同日同金额记录')
            row['intra_statement_duplicate'] = repeated[(row['card'],row['transaction_date'],row['amount'])] > 1
            if preview['warnings'] or not all(c['balance_ok'] for c in preview['cards']):
                reasons.append('账单校验未通过')
            action = 'match' if candidate else ('import' if not row['candidate_count'] else '')
            ready = bool(category and action and not reasons)
            row['review_group'] = ('ready_match' if candidate else 'ready_import') if ready else 'review'
            row['suggestion'] = {'action':action, 'transaction_id':candidate['id'] if candidate else None,
                'category':category, 'is_personal':personal, 'ready':ready, 'source':source, 'reasons':reasons}
        counts[row['review_group']] += 1
    preview['preprocessing'] = {'counts':dict(counts), 'method':'local_rules_and_confirmed_history', 'ai_used':False}
    return preview
