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
    document.querySelector('#period-label').textContent = data.month ? `${data.month.replace('-', ' 年 ')} 月个人支出` : '全部月份的个人支出';
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
      if (!row.is_personal) name.append(el('span', '非个人支出', 'personal-status'));
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
    '个人支出': row.is_personal ? '计入' : '不计入（保留对账记录）',
    '备注': row.note || '—',
    '原始描述': row.source_text,
    '识别提示': row.ambiguities.join('；') || '—',
  })) fields.append(el('dt', name), el('dd', value));
  if (row.statement) {
    for (const [name, value] of Object.entries({'银行商户': row.statement.merchant, '账单卡片':row.statement.card,
      '银行入账日':row.statement.posting_date, '账单日期':row.statement.statement_date})) fields.append(el('dt', name), el('dd', value));
  }
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
  document.querySelector('#statement-confirm').close();
  document.querySelector('#statement-panel').close();
  clearStatementPreview();
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
  if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) password.focus();
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken || ''},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) showAuth(false, '登录已过期，操作未完成。请重新登录后核对。');
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
    if (['transaction_date', 'amount', 'currency', 'payment_account'].includes(name)) input.readOnly = !!currentRecord.statement;
  }
  editForm.elements.is_personal.checked = currentRecord.is_personal;
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
  expense.is_personal = editForm.elements.is_personal.checked;
  document.querySelector('#detail-error').textContent = '';
  detailBusy(true);
  try {
    const table = currentRecord.state === 'pending' ? 'drafts' : 'transactions';
    await post(`/api/${table}/${currentRecord.id}/edit`, {revision: currentRecord.revision, expense});
    if (currentStatement && table === 'transactions') {
      for (const row of currentStatement.rows) if (row.processed?.transaction_id === currentRecord.id) row.processed.is_personal = expense.is_personal;
      renderStatement(currentStatement);
      document.querySelector('#statement-status').textContent = '账本记录已更新。未处理候选如有变动，请重新解析核对。';
    }
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
  if (!initialized || !csrfToken || loading || filtersDirty || statementBusy || document.visibilityState !== 'visible') return;
  if (document.querySelector('#detail').open || document.querySelector('#statement-panel').open || document.querySelector('#statement-confirm').open || document.activeElement?.closest('form, dialog, #records')) return;
  load({automatic: true});
}, 5000);

let statementBusy = false;
let statementGeneration = 0;
let currentStatement = null;
let statementSelection = null;
let statementPage = 1;
const statementChoices = new Map();
const statementForm = document.querySelector('#statement-form');
const statementPreview = document.querySelector('#statement-preview');
document.querySelector('#open-statement').addEventListener('click', () => document.querySelector('#statement-panel').showModal());
document.querySelector('#close-statement').addEventListener('click', () => document.querySelector('#statement-panel').close());
document.querySelector('#statement-panel').addEventListener('close', () => {
  if (!document.querySelector('#dashboard').hidden) document.querySelector('#open-statement').focus();
});
function clearStatementPreview() {
  statementGeneration++;
  currentStatement = null;
  statementChoices.clear();
  statementPage = 1;
  document.querySelector('#statement-batch').hidden = true;
  document.querySelector('#statement-pages').hidden = true;
  showStatementUpload(true);
  document.querySelector('#statement-title').textContent='上传账单';
  statementForm.reset();
  statementPreview.replaceChildren();
  statementPreview.hidden = true;
  document.querySelector('#statement-detected').hidden = true;
  document.querySelector('#statement-panel').classList.remove('has-preview');
  document.querySelector('#statement-error').textContent = '';
  document.querySelector('#statement-status').textContent = '';
  document.querySelector('#statement-file').removeAttribute('aria-invalid');
}
document.querySelector('#statement-clear').addEventListener('click', clearStatementPreview);
function showStatementUpload(show) {
  document.querySelector('#statement-upload-controls').hidden=!show;
  document.querySelector('#statement-change').setAttribute('aria-expanded',String(show));
  document.querySelector('#statement-change').textContent=show?'收起上传':'更换账单';
}
document.querySelector('#statement-change').addEventListener('click',()=>showStatementUpload(document.querySelector('#statement-upload-controls').hidden));
document.querySelector('#statement-file').addEventListener('change', () => {
  statementGeneration++;
  currentStatement = null;
  statementChoices.clear();
  statementPage = 1;
  document.querySelector('#statement-batch').hidden = true;
  document.querySelector('#statement-pages').hidden = true;
  statementPreview.replaceChildren();
  statementPreview.hidden = true;
  document.querySelector('#statement-detected').hidden = true;
  document.querySelector('#statement-panel').classList.remove('has-preview');
  document.querySelector('#statement-error').textContent = '';
  document.querySelector('#statement-status').textContent = '';
  document.querySelector('#statement-file').removeAttribute('aria-invalid');
});
statementForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (statementBusy) return;
  const file = document.querySelector('#statement-file').files[0];
  const errorLine = document.querySelector('#statement-error');
  const feedback = document.querySelector('#statement-status');
  errorLine.textContent = '';
  document.querySelector('#statement-batch').hidden = true;
  document.querySelector('#statement-pages').hidden = true;
  statementPreview.replaceChildren();
  statementPreview.hidden = true;
  document.querySelector('#statement-detected').hidden = true;
  document.querySelector('#statement-panel').classList.remove('has-preview');
  if (!file || file.size > 6 * 1024 * 1024 || !file.name.toLowerCase().endsWith('.pdf')) {
    errorLine.textContent = '请选择不超过 6 MB 的 PDF 文件。';
    document.querySelector('#statement-file').setAttribute('aria-invalid', 'true');
    return;
  }
  const generation = ++statementGeneration;
  statementBusy = true;
  document.querySelector('#statement-submit').disabled = true;
  statementForm.setAttribute('aria-busy', 'true');
  feedback.textContent = '正在本机识别银行与账单格式…';
  try {
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = () => reject(new Error('无法读取文件，请重新选择。'));
      reader.readAsDataURL(file);
    });
    if (generation !== statementGeneration) return;
    const data = await post('/api/statements/preview', {pdf_base64: encoded});
    if (generation !== statementGeneration) return;
    currentStatement = data;
    statementChoices.clear();
    statementPage = 1;
    document.querySelector('#statement-view').value = 'pending';
    renderStatement(data);
    feedback.textContent = '已完成本机预处理。检查建议与例外后，可一次确认所选整批。';
  } catch (error) {
    if (generation === statementGeneration) {
      feedback.textContent = '';
      errorLine.textContent = error.message;
    }
  } finally {
    statementBusy = false;
    document.querySelector('#statement-submit').disabled = false;
    statementForm.setAttribute('aria-busy', 'false');
    updateBatchSummary();
  }
});
function renderStatement(data) {
  showStatementUpload(false);
  document.querySelector('#statement-title').textContent='核对账单';
  document.querySelector('#statement-panel').classList.add('has-preview');
  document.querySelector('#statement-detected').hidden = false;
  document.querySelector('#statement-bank').textContent = `已识别：${data.bank === 'UOB SG' ? 'UOB 新加坡' : data.bank} · 信用卡账单`;
  document.querySelector('#statement-metadata').textContent = `账单日 ${data.statement_date} · ${data.cards.length} 张卡 · ${data.rows.length} 笔明细`;
  statementPreview.replaceChildren();
  document.querySelector('#statement-batch').hidden = false;
  const groupNames = {pending:'未处理消费',ready_match:'建议匹配',ready_import:'建议补录',review:'需要你看',all:'全部明细'};
  const pending = data.rows.filter(row => row.kind === 'charge' && !row.processed).length;
  for (const option of document.querySelector('#statement-view').options) {
    const count = option.value === 'pending' ? pending : option.value === 'all' ? data.rows.length : (data.preprocessing?.counts[option.value] || 0);
    option.textContent = `${groupNames[option.value]} (${count})`;
  }
  const bulkCategory = document.querySelector('#statement-bulk-category');
  const previousCategory = bulkCategory.value;
  bulkCategory.replaceChildren(el('option','请选择分类'));
  bulkCategory.options[0].value = '';
  for (const [key,name] of Object.entries(categoryNames)) if (!['transfer','cash_withdrawal'].includes(key)) {
    const option = el('option', name); option.value = key; bulkCategory.append(option);
  }
  bulkCategory.value = previousCategory;
  for (const row of data.rows) if (row.kind==='charge' && !row.processed) {
    const existing=statementChoices.get(row.row_key);
    if(existing && !existing.dirty && row.suggestion) {
      existing.action=row.suggestion.action==='match' ? String(row.suggestion.transaction_id) : row.suggestion.action;
      existing.category=row.suggestion.category || '';
      existing.is_personal=row.suggestion.is_personal;
      existing.selected=existing.selected && row.suggestion.ready;
    }
    getStatementChoice(row);
  }
  const filtered = filteredStatementRows();
  const pages = Math.max(1,Math.ceil(filtered.length/50));
  statementPage = Math.min(statementPage,pages);
  const visible = filtered.slice((statementPage-1)*50,statementPage*50);
  for (const warning of data.warnings) statementPreview.append(el('p', warning, 'notice'));
  if (!visible.length) statementPreview.append(el('p','此范围没有明细。可以切换查看范围，或上传下一份账单。','empty'));
  for (const card of data.cards) {
    const cardRows = visible.filter(item => item.card === card.account);
    if (!cardRows.length) continue;
    const group = el('details', undefined, 'statement-card');
    group.open = true;
    group.append(el('summary', `${card.name} • ${card.last_four} · ${card.row_count} 笔`));
    group.append(el('p', `上期余额 ${card.opening_balance ?? '未识别'} + 本期净变动 ${card.net_movement} = 本期余额 ${card.closing_balance ?? '未识别'} SGD`, 'muted'));
    group.append(el('p', card.balance_ok ? '余额校验通过（不代表每一行已核对）' : `余额校验未通过 · 差额 ${card.difference ?? '未知'} SGD`, card.balance_ok ? 'muted' : 'notice'));
    group.append(cardDefaultForm(data,card));
    for (const row of cardRows) {
      const line = el('article', undefined, 'statement-row');
      const heading = el('div', undefined, 'statement-row-header');
      if (row.kind==='charge' && !row.processed && !data.warnings.length) {
        const chooseLabel=el('label',undefined,'check-label');
        const choose=el('input'); choose.type='checkbox'; choose.checked=getStatementChoice(row).selected;
        choose.setAttribute('aria-label',`选择 ${row.merchant} ${row.amount} SGD`);
        choose.addEventListener('change',()=>{getStatementChoice(row).selected=choose.checked;getStatementChoice(row).dirty=true; updateBatchSummary();});
        chooseLabel.append(choose,el('strong',row.merchant)); heading.append(chooseLabel);
      } else heading.append(el('strong',row.merchant));
      heading.append(el('span', `${money(row.signed_amount)} SGD`, 'amount'));
      line.append(heading, el('p', `消费 ${row.transaction_date} · 入账 ${row.posting_date} · PDF 第 ${row.page} 页`, 'muted'));
      if (row.original_currency) line.append(el('p', `原币 ${row.original_amount} ${row.original_currency}；对账使用 SGD 结算金额。`, 'muted'));
      if (row.processed) {
        line.append(el('p', `已${row.processed.action === 'match' ? '匹配' : '补录'} · 记录 #${row.processed.transaction_id} · ${row.processed.is_personal ? '个人支出' : '非个人支出'}（可在交易详情修改归属）`, 'statement-processed'));
      } else if (row.kind !== 'charge') {
        line.append(el('p', row.kind === 'payment' ? '信用卡还款 · 非消费，不寻找消费匹配' : '贷记／退款 · 非新增消费，后续需核对原消费', 'muted'));
      } else if (!row.candidate_count) {
        if(row.review_group!=='ready_import') line.append(el('p', '未找到候选 · 不代表漏记：请检查日期、金额和具体卡片。', 'muted'));
      } else if (row.review_group==='ready_match') {
        line.append(el('p',`建议匹配 #${row.candidates[0].id}「${row.candidates[0].merchant}」`,'muted'));
      } else {
        line.append(el('p', `${row.candidate_count} 笔可能对应的记账记录${row.candidate_count > 5 ? '（仅显示前 5 笔）' : ''}`, 'muted'));
        const candidates = el('ul', undefined, 'statement-candidates');
        for (const candidate of row.candidates) {
          const item = el('li');
          item.append(el('p', `#${candidate.id} · ${candidate.merchant}`), el('p', `${candidate.transaction_date} · ${candidate.account} · ${candidate.amount} SGD`, 'muted'), el('p', candidate.reason, 'muted'));
          candidates.append(item);
        }
        line.append(candidates);
      }
      if (!row.processed && row.kind === 'charge' && !data.warnings.length && card.balance_ok) {
        const adjustment=el('details',undefined,'statement-adjust');
        const summary=el('summary',statementChoiceText(row));
        adjustment.append(summary,statementReviewForm(data,row,()=>{summary.textContent=statementChoiceText(row); updateBatchSummary();}));
        adjustment.open = row.review_group==='review';
        line.append(el('p', `${groupNames[row.review_group] || '待核对'}：${row.suggestion?.source || ''}${row.suggestion?.reasons?.length ? '；'+row.suggestion.reasons.join('；') : ''}`,row.review_group==='review'?'personal-status':'suggestion-label'),adjustment);
      }
      group.append(line);
    }
    statementPreview.append(group);
  }
  statementPreview.hidden = false;
  document.querySelector('#statement-pages').hidden = filtered.length<=50;
  document.querySelector('#statement-page').textContent = `第 ${statementPage} / ${pages} 页 · ${filtered.length} 笔`;
  document.querySelector('#statement-previous').disabled = statementPage<=1;
  document.querySelector('#statement-next').disabled = statementPage>=pages;
  updateBatchSummary();
}

function getStatementChoice(row) {
  if (!statementChoices.has(row.row_key)) {
    const suggestion=row.suggestion || {};
    statementChoices.set(row.row_key,{action:suggestion.action==='match' ? String(suggestion.transaction_id) : (suggestion.action || ''),
      category:suggestion.category || '',is_personal:suggestion.is_personal ?? true,acknowledge:false,selected:!!suggestion.ready,dirty:false});
  }
  return statementChoices.get(row.row_key);
}
function statementChoiceText(row) {
  const choice=getStatementChoice(row);
  return `${choice.action==='import' ? '补录' : choice.action ? `匹配 #${choice.action}` : '待选处理方式'} · ${categoryNames[choice.category] || '待选分类'} · ${choice.is_personal ? '个人支出' : '非个人支出'} · 展开调整`;
}
function filteredStatementRows() {
  const view=document.querySelector('#statement-view').value;
  return (currentStatement?.rows || []).filter(row=>view==='all' || (view==='pending' ? row.kind==='charge' && !row.processed : row.review_group===view));
}
function selectedStatementRows() {
  return (currentStatement?.rows || []).filter(row=>row.kind==='charge' && !row.processed && getStatementChoice(row).selected);
}
function formatBatchMoney(rows) {
  const cents=rows.reduce((total,row)=>total+BigInt(row.amount.replace('.','')),0n);
  return `${(cents/100n).toLocaleString('en-SG')}.${(cents%100n).toString().padStart(2,'0')}`;
}
function updateBatchSummary() {
  const rows=selectedStatementRows();
  const personal=rows.filter(row=>getStatementChoice(row).is_personal);
  document.querySelector('#statement-batch-summary').textContent=`已选 ${rows.length} 笔 · 所选个人消费 ${formatBatchMoney(personal)} SGD · 非个人 ${rows.length-personal.length} 笔`;
  document.querySelector('#statement-apply-category').textContent=rows.length ? `应用到所选 ${rows.length} 笔` : '应用分类';
  document.querySelector('#statement-confirm-batch').disabled=!rows.length || statementBusy || !!currentStatement?.warnings.length;
}
function rowConfirmationBody(row) {
  const choice=getStatementChoice(row);
  const candidate=row.candidates.find(c=>String(c.id)===choice.action);
  if (!choice.category || !choice.action || (choice.action!=='import' && !candidate)) throw new Error(`${row.merchant}：请补全处理方式和分类。`);
  if (choice.action==='import' && (row.possible_duplicate_count || row.intra_statement_duplicate) && !choice.acknowledge) throw new Error(`${row.merchant}：请展开核对，明确确认这不是重复消费。`);
  return {row_key:row.row_key,action:choice.action==='import'?'import':'match',category:choice.category,is_personal:choice.is_personal,
    transaction_id:candidate?.id,revision:candidate?.revision,acknowledge_possible_duplicate:choice.acknowledge};
}
document.querySelector('#statement-view').addEventListener('change',()=>{statementPage=1; if(currentStatement) renderStatement(currentStatement);});
for (const [id,step] of [['statement-previous',-1],['statement-next',1]]) document.querySelector(`#${id}`).addEventListener('click',()=>{statementPage+=step; renderStatement(currentStatement);});
for (const id of ['statement-select-ready','statement-select-visible','statement-select-none']) document.querySelector(`#${id}`).addEventListener('click',()=>{
  if(!currentStatement || statementBusy) return;
  const rows=id==='statement-select-visible' ? filteredStatementRows().slice((statementPage-1)*50,statementPage*50) : currentStatement.rows;
  for(const row of rows) if(row.kind==='charge' && !row.processed) {
    getStatementChoice(row).dirty=true;
    if(id==='statement-select-none') getStatementChoice(row).selected=false;
    else if(id==='statement-select-visible' || row.suggestion?.ready) getStatementChoice(row).selected=true;
  }
  renderStatement(currentStatement);
});
function applyToSelected(key,value) {
  if(statementBusy || !currentStatement) return;
  const selected=selectedStatementRows();
  if(!selected.length) {document.querySelector('#statement-error').textContent='请先选择要批改的明细。';return;}
  for(const row of selected) {getStatementChoice(row)[key]=value;getStatementChoice(row).dirty=true;}
  document.querySelector('#statement-error').textContent='';
  renderStatement(currentStatement);
}
document.querySelector('#statement-apply-category').addEventListener('click',()=>{
  const category=document.querySelector('#statement-bulk-category').value;
  if(!category) {document.querySelector('#statement-error').textContent='请选择批改分类。';return;}
  applyToSelected('category',category);
});
document.querySelector('#statement-mark-personal').addEventListener('click',()=>applyToSelected('is_personal',true));
document.querySelector('#statement-mark-other').addEventListener('click',()=>applyToSelected('is_personal',false));
document.querySelector('#statement-confirm-batch').addEventListener('click',()=>{
  if(statementBusy || !currentStatement) return;
  try {
    const rows=selectedStatementRows();
    if(!rows.length) throw new Error('请先选择要确认的明细。');
    if(currentStatement.warnings.length) throw new Error('账单校验未通过，不能确认。');
    const items=rows.map(rowConfirmationBody);
    const targets=items.filter(item=>item.action==='match').map(item=>item.transaction_id);
    if(new Set(targets).size!==targets.length) throw new Error('多行选择了同一笔记账，请先调整匹配。');
    const imports=items.filter(item=>item.action==='import').length;
    const personal=rows.filter(row=>getStatementChoice(row).is_personal);
    const categories={};
    for(const row of rows) {const name=categoryNames[getStatementChoice(row).category];categories[name]=(categories[name]||0)+1;}
    statementSelection={data:currentStatement,batch:true,button:document.querySelector('#statement-confirm-batch'),body:{preview_id:currentStatement.preview_id,items}};
    document.querySelector('#statement-confirm-title').textContent='确认所选整批';
    document.querySelector('#statement-confirm-save').textContent=`确认保存 ${rows.length} 笔`;
    document.querySelector('#statement-confirm-description').textContent=`确认所选 ${rows.length} 笔（包括其他分页和筛选中已选择的记录）\n匹配已有 ${rows.length-imports} 笔，不重复新增\n补录新记录 ${imports} 笔\n所选消费合计 ${formatBatchMoney(rows)} SGD\n其中个人消费 ${formatBatchMoney(personal)} SGD，非个人 ${rows.length-personal.length} 笔\n分类：${Object.entries(categories).map(([name,count])=>`${name} ${count} 笔`).join('、')}\n\n只处理本批选择，未选例外保留待核对。任一笔冲突或无效，整批不保存。`;
    document.querySelector('#statement-confirm-duplicate').hidden=true;
    document.querySelector('#statement-confirm-error').textContent='';
    document.querySelector('#statement-error').textContent='';
    document.querySelector('#statement-confirm').showModal();
  } catch(error) {document.querySelector('#statement-error').textContent=error.message;}
});
function cardDefaultForm(data,card) {
  const form=el('form',undefined,'card-default');
  const label=el('label',undefined,'check-label');
  const check=el('input');check.type='checkbox';check.checked=card.personal_default;
  label.append(check,document.createTextNode('本卡默认计入我的个人支出'));
  const save=el('button','保存本卡默认值');save.type='submit';
  form.append(label,el('p','保存到本机，仅应用于此卡未确认明细和以后上传；不会改动已入账记录。','muted'),save);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(statementBusy) return;
    statementBusy=true;save.disabled=true;
    try {
      const result=await post('/api/statements/card-default',{preview_id:data.preview_id,card:card.account,is_personal:check.checked});
      if(currentStatement!==data) return;
      for(const row of data.rows) if(row.card===card.account && row.kind==='charge' && !row.processed) {
        getStatementChoice(row).is_personal=check.checked;getStatementChoice(row).dirty=true;
      }
      currentStatement=result.preview;renderStatement(currentStatement);
      document.querySelector('#statement-status').textContent='本卡默认值已保存，只更新未确认明细。';
    } catch(error) {document.querySelector('#statement-error').textContent=error.message;}
    finally {statementBusy=false;save.disabled=false;updateBatchSummary();}
  });
  return form;
}

function statementReviewForm(data, row, onChange=()=>{}) {
  const review = el('form', undefined, 'statement-review');
  review.setAttribute('aria-label', `处理 ${row.merchant} ${row.amount} SGD`);
  const saved = getStatementChoice(row);
  const actionLabel = el('label', '处理方式');
  const action = el('select'); action.required = true;
  const option = (value, label) => {const item = el('option', label); item.value = value; return item;};
  action.append(option('', '请选择'), option('import', '补录为新记录'));
  for (const candidate of row.candidates) action.append(option(String(candidate.id), `匹配 #${candidate.id} · ${candidate.merchant} · ${candidate.account}`));
  action.value = saved.action; actionLabel.append(action);
  const categoryLabel = el('label', '分类');
  const category = el('select'); category.required = true;
  category.append(option('', '请选择分类'));
  for (const [key, name] of Object.entries(categoryNames)) if (!['transfer','cash_withdrawal'].includes(key)) category.append(option(key, name));
  category.value = saved.category; categoryLabel.append(category);
  const personalLabel = el('label', undefined, 'check-label');
  const personal = el('input'); personal.type = 'checkbox'; personal.checked = saved.is_personal;
  personalLabel.append(personal, document.createTextNode('计入我的个人支出'));
  const duplicateLabel = el('label', undefined, 'check-label');
  const duplicate = el('input'); duplicate.type = 'checkbox'; duplicate.checked = saved.acknowledge;
  duplicateLabel.append(duplicate, document.createTextNode('我核对过，这是另一笔消费，不是上面的重复记录'));
  const sync = (changed=false) => {
    duplicateLabel.hidden = action.value !== 'import' || !(row.possible_duplicate_count || row.intra_statement_duplicate);
    duplicate.required = !duplicateLabel.hidden;
    statementChoices.set(row.row_key, {...getStatementChoice(row), action:action.value, category:category.value, is_personal:personal.checked, acknowledge:duplicate.checked,dirty:getStatementChoice(row).dirty || !!changed});
    onChange();
  };
  action.addEventListener('change', () => {
    const candidate = row.candidates.find(item => String(item.id) === action.value);
    if (candidate) {category.value = candidate.category; personal.checked = data.cards.find(c=>c.account===row.card)?.personal_default_saved ? data.cards.find(c=>c.account===row.card).personal_default : candidate.is_personal;}
    sync(true);
  });
  for (const input of [category, personal, duplicate]) input.addEventListener('change', sync);
  sync();
  const button = el('button', '核对并确认'); button.type = 'submit';
  review.append(actionLabel, categoryLabel, personalLabel, duplicateLabel, button);
  review.addEventListener('submit', event => {
    event.preventDefault();
    const candidate = row.candidates.find(item => String(item.id) === action.value);
    statementSelection = {data, row, button, candidate, body:{preview_id:data.preview_id, row_key:row.row_key,
      action:action.value === 'import' ? 'import' : 'match', category:category.value, is_personal:personal.checked,
      transaction_id:candidate?.id, revision:candidate?.revision, acknowledge_possible_duplicate:duplicate.checked}};
    document.querySelector('#statement-confirm-title').textContent='确认这笔账单明细';
    document.querySelector('#statement-confirm-save').textContent='确认保存';
    document.querySelector('#statement-confirm-description').textContent = `${row.merchant}\n${row.amount} SGD · ${row.card}\n消费 ${row.transaction_date} · 入账 ${row.posting_date}\n${candidate ? `关联已有记录 #${candidate.id}「${candidate.merchant}」，不新增交易` : '补录一笔新记录'}\n分类：${categoryNames[category.value]}\n${personal.checked ? '计入我的个人支出' : '非个人支出：保留对账记录，不计入我的统计'}`;
    document.querySelector('#statement-confirm-duplicate').hidden = action.value !== 'import' || !(row.possible_duplicate_count || row.intra_statement_duplicate);
    document.querySelector('#statement-confirm-duplicate').textContent = '已有同金额、近日期记录。你选择补录另一笔，请再次确认不是重复消费。';
    document.querySelector('#statement-confirm-error').textContent = '';
    document.querySelector('#statement-confirm').showModal();
  });
  return review;
}
document.querySelector('#statement-confirm-back').addEventListener('click', () => document.querySelector('#statement-confirm').close());
document.querySelector('#statement-confirm').addEventListener('close', () => statementSelection?.button?.isConnected && statementSelection.button.focus());
document.querySelector('#statement-confirm-save').addEventListener('click', async () => {
  if (!statementSelection || statementBusy) return;
  const selection = statementSelection;
  const dialog = document.querySelector('#statement-confirm');
  statementBusy = true;
  for (const button of dialog.querySelectorAll('button')) button.disabled = true;
  dialog.setAttribute('aria-busy', 'true');
  try {
    const result = await post(selection.batch ? '/api/statements/confirm-batch' : '/api/statements/confirm', selection.body);
    if (currentStatement !== selection.data) return;
    currentStatement = result.preview;
    dialog.close();
    renderStatement(currentStatement);
    document.querySelector('#statement-status').textContent = selection.batch ? `整批 ${result.count} 笔已保存。匹配不重复增加交易，非个人消费不计入个人统计。` : `已${result.action === 'match' ? '匹配' : '补录'}记录 #${result.transaction_id} · ${result.is_personal ? '计入个人支出' : '非个人支出，不计入统计'}。`;
    document.querySelector('#statement-status').setAttribute('tabindex', '-1');
    document.querySelector('#statement-status').focus();
    await load();
  } catch (error) {document.querySelector('#statement-confirm-error').textContent = error.message;}
  finally {
    statementBusy = false;
    dialog.setAttribute('aria-busy', 'false');
    for (const button of dialog.querySelectorAll('button')) button.disabled = false;
    updateBatchSummary();
  }
});
