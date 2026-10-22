// 필터와 거래 상세 화면을 안전한 DOM API로 표시합니다.
const form = document.querySelector('#filters');
const statusLine = document.querySelector('#status');
const content = document.querySelector('#content');
let state = 'confirmed';
let page = 1;
let pages = 1;
let controller;
let loading = false;
let filtersDirty = false;
let initialized = false;
let categoryNames = {};
const money = value => Number(value).toLocaleString('en-SG', {minimumFractionDigits: 2, maximumFractionDigits: 2});
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function fillOptions(name, values, labels = {}) {
  const select = form.elements[name];
  const selected = select.value;
  select.replaceChildren(select.options[0]);
  for (const value of values) {
    const option = el('option', labels[value] || value);
    option.value = value;
    select.append(option);
  }
  select.value = selected;
}
async function load({automatic = false} = {}) {
  if (controller) controller.abort();
  controller = new AbortController();
  const requestController = controller;
  loading = true;
  const params = new URLSearchParams(new FormData(form));
  params.set('state', state);
  params.set('page', page);
  if (!automatic) statusLine.textContent = '正在更新账本…';
  statusLine.className = 'status';
  content.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(`/api/ledger?${params}`, {signal: controller.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法读取账本');
    categoryNames = data.options.categories;
    fillOptions('account', [...data.options.accounts, '__unknown__'], {'__unknown__': '未注明账户'});
    fillOptions('currency', data.options.currencies);
    fillOptions('category', Object.keys(categoryNames), categoryNames);
    document.querySelector('#period-label').textContent = data.month ? `${data.month.replace('-', ' 年 ')} 月已确认消费` : '全部月份的已确认消费';
    const activeFilters = ['account', 'currency', 'category'].filter(name => form.elements[name].value).length;
    document.querySelector('#filter-label').textContent = activeFilters ? `更多筛选 (${activeFilters})` : '更多筛选';
    const totals = document.querySelector('#totals');
    totals.replaceChildren();
    const sortedTotals = Object.entries(data.totals).sort(([a], [b]) => (a === 'SGD' ? -1 : b === 'SGD' ? 1 : a.localeCompare(b)));
    for (const [currency, amount] of sortedTotals) {
      const block = el('div', undefined, 'money-block');
      block.append(el('div', currency === 'SGD' ? '新加坡元' : currency === 'MYR' ? '马来西亚令吉' : currency, 'currency-label'));
      const value = el('div', undefined, 'money');
      value.append(el('small', currency === 'SGD' ? 'S$' : currency === 'MYR' ? 'RM' : currency), document.createTextNode(money(amount)));
      block.append(value);
      totals.append(block);
    }
    document.querySelector('#confirmed-tab').textContent = `已确认 ${data.confirmed_count}`;
    document.querySelector('#pending-tab').textContent = `待确认 ${data.pending_count}`;
    document.querySelector('#confirmed-tab').setAttribute('aria-pressed', state === 'confirmed');
    document.querySelector('#pending-tab').setAttribute('aria-pressed', state === 'pending');
    document.querySelector('#pending-note').hidden = state !== 'pending';
    document.querySelector('#record-count').textContent = `${data.total} 笔记录`;
    const records = document.querySelector('#records');
    records.replaceChildren();
    for (const row of data.records) {
      const button = el('button', undefined, 'transaction');
      button.type = 'button';
      const name = el('span');
      name.append(el('strong', row.merchant), el('small', row.transaction_date));
      const classification = el('span', undefined, 'classification');
      classification.append(el('span', categoryNames[row.category] || row.category, 'category-label'), el('small', row.payment_account || '未注明账户'));
      const amount = el('span', money(row.amount), 'amount');
      amount.append(el('small', row.currency));
      button.append(name, classification, amount);
      button.addEventListener('click', () => showDetail(row));
      records.append(button);
    }
    if (!data.records.length) {
      const empty = el('div', undefined, 'empty');
      empty.append(el('strong', state === 'pending' ? '暂无待确认记录' : '暂无符合条件的记录'), el('span', '调整筛选，或到 Telegram 添加记录。'));
      records.append(empty);
    }
    const categories = document.querySelector('#categories');
    categories.replaceChildren();
    for (const currency of data.options.currencies) {
      const rows = data.categories.filter(row => row.currency === currency);
      if (!rows.length) continue;
      const group = el('section', undefined, 'category-group');
      group.append(el('h3', currency));
      for (const row of rows) {
        const line = el('div', undefined, 'category-row');
        line.append(el('span', categoryNames[row.category] || row.category), el('span', money(row.amount)));
        group.append(line);
      }
      categories.append(group);
    }
    if (!data.categories.length) categories.append(el('p', '当前范围还没有已确认消费。', 'empty'));
    page = data.page;
    pages = data.pages;
    document.querySelector('#previous').disabled = page <= 1;
    document.querySelector('#next').disabled = page >= pages;
    document.querySelector('#page-label').textContent = `第 ${page} / ${pages} 页`;
    document.querySelector('#updated').textContent = `更新于 ${new Date(data.updated_at).toLocaleTimeString('zh-CN')}`;
    statusLine.textContent = '';
    filtersDirty = false;
    content.hidden = false;
  } catch (error) {
    if (error.name === 'AbortError') return;
    // Hide stale amounts when fresh data cannot be loaded.
    content.hidden = true;
    statusLine.textContent = `读取失败：${error.message}。请点“刷新账本”重试。`;
    statusLine.className = 'status error';
  } finally {
    if (controller === requestController) {
      loading = false;
      content.setAttribute('aria-busy', 'false');
    }
  }
}
function showDetail(row) {
  document.querySelector('#detail-title').textContent = row.merchant;
  document.querySelector('#detail-amount').textContent = `${row.currency} ${money(row.amount)}`;
  const fields = document.querySelector('#detail-fields');
  fields.replaceChildren();
  for (const [name, value] of Object.entries({
    '消费日期': row.transaction_date,
    '付款账户': row.payment_account || '未注明',
    '分类': categoryNames[row.category] || row.category,
    '状态': row.state === 'confirmed' ? '已确认' : '待确认 · 尚未入账',
    '备注': row.note || '—',
    '原始描述': row.source_text,
    '识别提示': row.ambiguities.join('；') || '—',
  })) fields.append(el('dt', name), el('dd', value));
  document.querySelector('#detail').showModal();
}
form.addEventListener('submit', event => {event.preventDefault(); page = 1; load();});
form.addEventListener('input', () => {filtersDirty = true;});
form.addEventListener('change', () => {filtersDirty = true;});
document.querySelector('#refresh').addEventListener('click', load);
document.querySelector('#clear-filters').addEventListener('click', () => {form.reset(); form.elements.month.value = ''; page = 1; load();});
for (const tab of ['confirmed', 'pending']) document.querySelector(`#${tab}-tab`).addEventListener('click', () => {state = tab; page = 1; load();});
document.querySelector('#previous').addEventListener('click', () => {if (page > 1) {page--; load();}});
document.querySelector('#next').addEventListener('click', () => {if (page < pages) {page++; load();}});
document.querySelector('#close-detail').addEventListener('click', () => document.querySelector('#detail').close());
// The server chooses the default month using APP_TIMEZONE, not the phone's timezone.
fetch('/api/ledger', {cache: 'no-store'}).then(r => {if (!r.ok) throw new Error(); return r.json();}).then(data => {
  form.elements.month.value = data.month;
  return load();
}).catch(() => load()).finally(() => {initialized = true;});

// Poll only the local ledger; never interrupt editing, details, focus, or an active request.
setInterval(() => {
  if (!initialized || loading || filtersDirty || document.visibilityState !== 'visible') return;
  if (document.querySelector('#detail').open || document.activeElement?.closest('form, dialog, #records')) return;
  load({automatic: true});
}, 5000);
