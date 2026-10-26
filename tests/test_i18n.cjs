// 언어 전환의 저장 복원과 사용자 텍스트 보존을 검증합니다.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/finance_bot/static/i18n.js', 'utf8');
function fixture(saved, blocked = false) {
  const storage = new Map(saved ? [['finance-language', saved]] : []);
  const context = {module: {exports: {}}, localStorage: {
    getItem(key) { if (blocked) throw Error('blocked'); return storage.get(key); },
    setItem(key, value) { if (blocked) throw Error('blocked'); storage.set(key, value); },
  }};
  vm.runInNewContext(source, context);
  return {api: context.module.exports, storage, context};
}
test('defaults to Chinese and restores only a supported preference', () => {
  assert.equal(fixture().api.t('餐饮'), '餐饮');
  assert.equal(fixture('en').api.t('餐饮'), 'Dining');
  assert.equal(fixture('unknown').api.locale(), 'zh-CN');
});
test('persists only language and works when browser storage is blocked', () => {
  const {api, storage} = fixture();
  api.setLanguage('en');
  assert.deepEqual([...storage], [['finance-language', 'en']]);
  assert.equal(api.locale(), 'en-SG');
  const blocked = fixture(null, true).api;
  blocked.setLanguage('en');
  assert.equal(blocked.t('密码不正确，请重试。'), 'Incorrect password. Please try again.');
});
test('interpolates values as text without translating or interpreting user content', () => {
  const {api} = fixture('en');
  const merchant = '<script>餐饮 ${1}</script>';
  assert.equal(api.t`删除「${merchant}」${'SGD'} ${'12.00'}？移至已删除后不计入统计，可随时恢复。`,
    `Delete “${merchant}” (SGD 12.00)? It will be excluded from totals and can be restored anytime.`);
  assert.equal(api.t('午餐 at Kopitiam'), '午餐 at Kopitiam');
  assert.equal(api.t('__proto__'), '__proto__');
});
test('switches known status messages back to Chinese', () => {
  const {api} = fixture('en');
  const message = api.t('修改已保存。');
  api.setLanguage('zh-Hans');
  assert.equal(api.t(message), '修改已保存。');
});
test('all annotated static UI and basic dynamic UI have translations', () => {
  const {api} = fixture('en');
  const html = fs.readFileSync('src/finance_bot/static/index.html', 'utf8');
  const app = fs.readFileSync('src/finance_bot/static/app.js', 'utf8').split('let statementBusy = false;')[0];
  const strings = [...html.matchAll(/data-i18n(?:-placeholder|-aria-label)?="([^"]+)"/g)].map(m => m[1]);
  strings.push(...[...app.matchAll(/\bt\('([^']+)'\)/g)].map(m => m[1]));
  // Statement-only feedback remains out of scope while that feature is paused.
  for (const text of strings.filter(text => !text.startsWith('账本记录已更新。'))) assert.notEqual(api.t(text), text, text);
  assert.equal(api.t`第 ${2} / ${4} 页`, 'Page 2 / 4');
  assert.equal(api.t`${2026} 年 ${'10'} 月个人支出`, 'Personal spending · 2026-10');
});
test('updates only marked UI nodes, document language and selected option', () => {
  const {api, context} = fixture('en');
  const node = {textContent: '餐饮', getAttribute: () => '餐饮'};
  const original = {textContent: '餐饮'};
  const select = {};
  context.document = {documentElement: {},
    querySelectorAll: selector => selector === '[data-i18n]' ? [node] : [],
    getElementById: () => select};
  api.applyLanguage();
  assert.equal(node.textContent, 'Dining');
  assert.equal(original.textContent, '餐饮');
  assert.equal(context.document.documentElement.lang, 'en');
  assert.equal(select.value, 'en');
});
