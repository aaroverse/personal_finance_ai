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
let csrfToken = null;
let needsSetup = false;
let currentRecord = null;
let reviewAction = null;
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
    if (response.status === 401) {showAuth(false, '登录已过期，请重新登录。'); return;}
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
  currentRecord = row;
  document.querySelector('#detail-error').textContent = '';
  document.querySelector('#edit-form').hidden = true;
  document.querySelector('#detail-fields').hidden = false;
  document.querySelector('#detail-actions').hidden = false;
  document.querySelector('#review-check').hidden = true;
  document.querySelector('#confirm-record').hidden = row.state !== 'pending';
  document.querySelector('#cancel-record').hidden = row.state !== 'pending';
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

function showAuth(setup, message = '') {
  csrfToken = null;
  initialized = false;
  needsSetup = setup;
  document.querySelector('#detail').close();
  document.querySelector('#dashboard').hidden = true;
  document.querySelector('#auth-panel').hidden = false;
  document.querySelector('#auth-title').textContent = setup ? '设置账本密码' : '登录账本';
  document.querySelector('#auth-description').textContent = setup ? '首次使用，请设置至少 12 个字符的页面密码。密码仅保存在本机，不是银行或 Telegram 密码。' : '输入本机页面密码，查看和管理账本。';
  document.querySelector('#auth-submit').textContent = setup ? '设置并进入' : '登录';
  document.querySelector('#auth-confirm-label').hidden = !setup;
  const password = document.querySelector('#auth-password');
  password.minLength = setup ? 12 : 1;
  password.autocomplete = setup ? 'new-password' : 'current-password';
  document.querySelector('#auth-confirm').required = setup;
  document.querySelector('#auth-error').textContent = message;
  password.focus();
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken || ''},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) showAuth(false, '登录已过期，编辑尚未保存。请重新登录。');
    throw Object.assign(new Error(data.error || '操作失败，请重试。'), {fields: data.fields || {}});
  }
  return data;
}

async function openDashboard() {
  document.querySelector('#auth-panel').hidden = true;
  document.querySelector('#dashboard').hidden = false;
  // Resolve the initial month in APP_TIMEZONE on the server.
  const response = await fetch('/api/ledger', {cache: 'no-store'});
  if (response.status === 401) {showAuth(false); return;}
  if (response.ok) form.elements.month.value = (await response.json()).month;
  await load();
  initialized = true;
}

document.querySelector('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const password = document.querySelector('#auth-password');
  const confirmation = document.querySelector('#auth-confirm');
  const error = document.querySelector('#auth-error');
  error.textContent = '';
  password.removeAttribute('aria-invalid');
  confirmation.removeAttribute('aria-invalid');
  if (needsSetup && password.value !== confirmation.value) {
    confirmation.setAttribute('aria-invalid', 'true');
    error.textContent = '两次输入的密码不一致。';
    confirmation.focus();
    return;
  }
  const submit = document.querySelector('#auth-submit');
  submit.disabled = true;
  event.currentTarget.setAttribute('aria-busy', 'true');
  try {
    const result = await post(needsSetup ? '/api/setup' : '/api/login', {password: password.value});
    csrfToken = result.csrf;
    password.value = '';
    confirmation.value = '';
    await openDashboard();
  } catch (failure) {
    error.textContent = failure.message;
    password.setAttribute('aria-invalid', 'true');
  } finally {
    submit.disabled = false;
    document.querySelector('#auth-form').setAttribute('aria-busy', 'false');
  }
});

document.querySelector('#logout').addEventListener('click', async () => {
  try {await post('/api/logout', {}); showAuth(false);}
  catch (error) {statusLine.textContent = error.message;}
});

document.querySelector('#edit-start').addEventListener('click', () => {
  const editForm = document.querySelector('#edit-form');
  for (const name of ['transaction_date', 'amount', 'currency', 'merchant', 'category', 'payment_account', 'note']) {
    const input = editForm.elements[name];
    input.removeAttribute('aria-invalid');
    document.querySelector(`#error-${name}`).textContent = '';
    if (name === 'category') {
      input.replaceChildren();
      for (const [value, label] of Object.entries(categoryNames)) {
        const option = el('option', label); option.value = value; input.append(option);
      }
    }
    input.value = currentRecord[name] || '';
  }
  const suggestions = document.querySelector('#account-suggestions');
  suggestions.replaceChildren();
  for (const option of form.elements.account.options) {
    if (!option.value || option.value === '__unknown__') continue;
    const suggestion = el('option'); suggestion.value = option.value; suggestions.append(suggestion);
  }
  document.querySelector('#edit-source').textContent = currentRecord.source_text;
  document.querySelector('#detail-fields').hidden = true;
  document.querySelector('#detail-actions').hidden = true;
  document.querySelector('#detail-error').textContent = '';
  editForm.hidden = false;
  editForm.elements.merchant.focus();
});

document.querySelector('#edit-cancel').addEventListener('click', () => {
  document.querySelector('#edit-form').hidden = true;
  document.querySelector('#detail-fields').hidden = false;
  document.querySelector('#detail-actions').hidden = false;
  document.querySelector('#detail-error').textContent = '';
  document.querySelector('#edit-start').focus();
});

function detailBusy(busy) {
  document.querySelector('#detail').setAttribute('aria-busy', String(busy));
  for (const input of document.querySelectorAll('#detail button, #detail input, #detail select, #detail textarea')) input.disabled = busy;
}

document.querySelector('#edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  const editForm = event.currentTarget;
  const expense = Object.fromEntries(new FormData(editForm));
  expense.payment_account = expense.payment_account.trim() || null;
  expense.note = expense.note.trim() || null;
  for (const name of Object.keys(expense)) {
    editForm.elements[name].removeAttribute('aria-invalid');
    document.querySelector(`#error-${name}`).textContent = '';
  }
  document.querySelector('#detail-error').textContent = '';
  detailBusy(true);
  try {
    const table = currentRecord.state === 'pending' ? 'drafts' : 'transactions';
    await post(`/api/${table}/${currentRecord.id}/edit`, {revision: currentRecord.revision, expense});
    document.querySelector('#detail').close();
    await load();
    statusLine.textContent = currentRecord.state === 'pending' ? '草稿修改已保存，仍需确认入账。' : '修改已保存。';
  } catch (error) {
    document.querySelector('#detail-error').textContent = error.message;
    for (const name of Object.keys(error.fields || {})) {
      if (!editForm.elements[name]) continue;
      editForm.elements[name].setAttribute('aria-invalid', 'true');
      document.querySelector(`#error-${name}`).textContent = name === 'amount' ? '金额需大于零，且最多两位小数。' : '请检查此字段的格式与长度。';
    }
  } finally {
    detailBusy(false);
    if (document.querySelector('#detail').open) editForm.querySelector('[aria-invalid=true]')?.focus();
  }
});

document.querySelector('#detail').addEventListener('cancel', event => {
  if (document.querySelector('#detail').getAttribute('aria-busy') === 'true') event.preventDefault();
});

for (const action of ['confirm', 'cancel']) {
  document.querySelector(`#${action}-record`).addEventListener('click', () => {
    reviewAction = action;
    document.querySelector('#detail-actions').hidden = true;
    document.querySelector('#review-check').hidden = false;
    document.querySelector('#review-message').textContent = action === 'confirm' ? '确认这笔金额、分类和账户正确，并写入账本？' : '取消这份草稿？取消后不会计入支出。';
    document.querySelector('#review-submit').textContent = action === 'confirm' ? '确认入账' : '确定取消草稿';
    document.querySelector('#review-submit').focus();
  });
}
document.querySelector('#review-back').addEventListener('click', () => {
  document.querySelector('#review-check').hidden = true;
  document.querySelector('#detail-actions').hidden = false;
  document.querySelector(`#${reviewAction}-record`).focus();
});
document.querySelector('#review-submit').addEventListener('click', async () => {
  detailBusy(true);
  document.querySelector('#detail-error').textContent = '';
  try {
    await post(`/api/drafts/${currentRecord.id}/${reviewAction}`, {revision: currentRecord.revision});
    document.querySelector('#detail').close();
    await load();
    statusLine.textContent = reviewAction === 'confirm' ? '已确认入账。' : '草稿已取消。';
  } catch (error) {document.querySelector('#detail-error').textContent = error.message;}
  finally {detailBusy(false);}
});

fetch('/api/session', {cache: 'no-store'}).then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(session => {
  if (!session.authenticated) {showAuth(session.needs_setup); return;}
  csrfToken = session.csrf;
  return openDashboard();
}).catch(() => showAuth(false, '无法连接账本服务，请确认服务正在运行后刷新页面。'));

// Poll only the local ledger; never interrupt editing, details, focus, or an active request.
setInterval(() => {
  if (!initialized || !csrfToken || loading || filtersDirty || document.visibilityState !== 'visible') return;
  if (document.querySelector('#detail').open || document.activeElement?.closest('form, dialog, #records')) return;
  load({automatic: true});
}, 5000);
