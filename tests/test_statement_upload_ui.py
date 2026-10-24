# 은행별 고정 섹션 없이 접근 가능한 통합 명세서 업로드 진입점을 검증합니다.
from html.parser import HTMLParser

from finance_bot.web import ASSETS


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids[values['id']] = (tag, values)


def test_unified_upload_is_a_dialog_not_a_bank_specific_ledger_section():
    source = (ASSETS / 'index.html').read_text()
    page = Elements(); page.feed(source)
    tag, button = page.ids['open-statement']
    assert tag == 'button' and button['aria-haspopup'] == 'dialog'
    assert button['aria-controls'] == 'statement-panel'
    tag, panel = page.ids['statement-panel']
    assert tag == 'dialog' and panel['aria-labelledby'] == 'statement-title'
    assert '信用卡账单 · UOB 新加坡' not in source
    assert 'statement-detected' in page.ids
    assert page.ids['statement-file'][1]['aria-describedby'] == 'statement-help statement-support statement-error'
