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
    document.querySelector('#open-statement').hidden=!data.features?.statements;
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
    document.querySelector('#deleted-tab').textContent = `已删除 ${data.deleted_count || 0}`;
    document.querySelector('#confirmed-tab').setAttribute('aria-pressed', state === 'confirmed');
    document.querySelector('#pending-tab').setAttribute('aria-pressed', state === 'pending');
    document.querySelector('#deleted-tab').setAttribute('aria-pressed',state==='deleted');
    document.querySelector('#pending-note').hidden = state === 'confirmed';
    document.querySelector('#pending-note').textContent=state==='deleted'?'已删除记录不计入统计。打开一笔记录可恢复。':'点击一笔草稿，检查后确认入账或取消。';
    document.querySelector('#record-count').textContent = `${data.total} 笔记录`;
    const records = document.querySelector('#records');
    records.replaceChildren();
    for (const row of data.records) {
      const button = el('button', undefined, 'transaction');
      button.type = 'button';
      const name = el('span');
      name.append(el('strong', row.merchant), el('small', row.transaction_date));
      if (!row.is_personal) name.append(el('span', '非个人支出', 'personal-status'));
      if(row.state==='deleted')name.append(el('span','已删除 · 可恢复','personal-status'));
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
  document.querySelector('#edit-start').hidden=row.state==='deleted';
  document.querySelector('#delete-record').hidden=row.state!=='confirmed';
  document.querySelector('#restore-record').hidden=row.state!=='deleted';
  document.querySelector('#detail-title').textContent = row.merchant;
  document.querySelector('#detail-amount').textContent = `${row.currency} ${money(row.amount)}`;
  const fields = document.querySelector('#detail-fields');
  fields.replaceChildren();
  for (const [name, value] of Object.entries({
    '消费日期': row.transaction_date,
    '付款账户': row.payment_account || '未注明',
    '分类': categoryNames[row.category] || row.category,
    '状态': row.state === 'deleted'?'已删除 · 不计入统计':row.state === 'confirmed' ? '已确认' : '待确认 · 尚未入账',
    '个人支出': row.is_personal ? '计入' : '不计入（保留对账记录）',
    '备注': row.note || '—',
    '原始描述': row.source_text,
    '识别提示': row.ambiguities.join('；') || '—',
  })) fields.append(el('dt', name), el('dd', value));
  if(row.deleted_at)fields.append(el('dt','删除时间'),el('dd',new Date(row.deleted_at).toLocaleString('zh-CN')));
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
for (const tab of ['confirmed', 'pending','deleted']) document.querySelector(`#${tab}-tab`).addEventListener('click', () => {state = tab; page = 1;if(tab==='deleted')form.elements.month.value=''; load();});
document.querySelector('#previous').addEventListener('click', () => {if (page > 1) {page--; load();}});
document.querySelector('#next').addEventListener('click', () => {if (page < pages) {page++; load();}});
document.querySelector('#close-detail').addEventListener('click', () => document.querySelector('#detail').close());

function showAuth(setup, message = '') {
  document.querySelector('#statement-history').close();
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
    throw Object.assign(new Error(data.error || '操作失败，请重试。'), {fields: data.fields || {},status:response.status});
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
  try {await flushStatementProgress();await post('/api/logout', {}); showAuth(false);}
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

for (const action of ['confirm', 'cancel','delete','restore']) {
  document.querySelector(`#${action}-record`).addEventListener('click', () => {
    reviewAction = action;
    document.querySelector('#detail-actions').hidden = true;
    document.querySelector('#review-check').hidden = false;
    const messages={confirm:'确认这笔金额、分类和账户正确，并写入账本？',cancel:'取消这份草稿？取消后不会计入支出。',
      delete:`删除「${currentRecord.merchant}」${currentRecord.currency} ${money(currentRecord.amount)}？移至已删除后不计入统计，可随时恢复。`,
      restore:`恢复「${currentRecord.merchant}」${currentRecord.currency} ${money(currentRecord.amount)}？${currentRecord.is_personal?'恢复后重新计入个人支出。':'此笔为非个人支出，个人统计金额不变。'}`};
    document.querySelector('#review-message').textContent=messages[action];
    document.querySelector('#review-submit').textContent = {confirm:'确认入账',cancel:'确定取消草稿',delete:'确认删除',restore:'确认恢复'}[action];
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
    const table=['delete','restore'].includes(reviewAction)?'transactions':'drafts';
    await post(`/api/${table}/${currentRecord.id}/${reviewAction}`, {revision: currentRecord.revision});
    document.querySelector('#detail').close();
    await load();
    statusLine.textContent={confirm:'已确认入账。',cancel:'草稿已取消。',delete:'已移到已删除，不计入统计；可恢复。',restore:'记录已恢复。'}[reviewAction];
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
let statementPhase = 'ownership';
let statementUndo = null;
const statementAppliedCards = new Set();
const statementCardDrafts = new Map();
const statementChoices = new Map();
let progressDirty=0,progressSaved=0,progressPromise=null,progressError=null;
let historyPage=1,historyPages=1,historyBusy=false;

function progressSnapshot() {
  const choices={};
  for(const row of currentStatement.rows) if(row.kind==='charge'&&!row.processed) choices[row.row_key]={...getStatementChoice(row)};
  return {preview_id:currentStatement.preview_id,choices,phase:statementPhase,
    applied_cards:[...statementAppliedCards],card_drafts:Object.fromEntries(statementCardDrafts)};
}
function showProgressStatus() {
  const section=document.querySelector('#statement-progress');
  section.hidden=!currentStatement?.review_id;
  const text=document.querySelector('#statement-progress-status');
  text.textContent=progressError?.status===401?'登录已过期，本页最新更改尚未保存。重新登录将恢复上次保存的进度。':progressError ? `进度未保存：${progressError.message}` : progressSaved<progressDirty?'正在保存核对进度…':'进度已保存到本机 · 尚未确认的明细不会入账';
  text.classList.toggle('form-error',!!progressError);
  const retry=document.querySelector('#statement-progress-retry');retry.hidden=!progressError;
  retry.textContent=progressError?.status===409?'查看较新进度':progressError?.status===401?'重新登录，恢复已存进度':'重试保存进度';
}
function queueStatementProgress() {
  if(!currentStatement?.review_id)return;
  progressDirty++;
  startProgressSave();
}
function startProgressSave() {
  showProgressStatus();
  if(progressPromise||progressError||!currentStatement?.review_id||progressSaved>=progressDirty)return;
  const data=currentStatement,generation=statementGeneration;
  progressPromise=(async()=>{
    while(progressSaved<progressDirty&&currentStatement===data&&generation===statementGeneration) {
      const sequence=progressDirty,body=progressSnapshot();
      try {
        // A failed autosave must retain in-memory edits instead of clearing them on login expiry.
        const response=await fetch('/api/statements/progress',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken||''},body:JSON.stringify(body)});
        const result=await response.json();
        if(!response.ok)throw Object.assign(new Error(result.error||'请检查连接后重试。'),{status:response.status});
        if(currentStatement!==data||generation!==statementGeneration)return;
        data.preview_id=result.preview_id;data.progress_version=result.progress_version;
        progressSaved=sequence;
      } catch(error){if(currentStatement===data)progressError=error;return;}
    }
  })().finally(()=>{progressPromise=null;showProgressStatus();if(!progressError&&currentStatement?.review_id&&progressSaved<progressDirty)startProgressSave();});
}
async function flushStatementProgress() {
  startProgressSave();
  while(progressPromise)await progressPromise;
  if(progressError)throw new Error('核对进度尚未保存。'+progressError.message);
}
window.addEventListener('beforeunload',event=>{
  if(currentStatement?.review_id&&progressSaved<progressDirty){event.preventDefault();event.returnValue='';}
});
document.querySelector('#statement-progress-retry').addEventListener('click',async()=>{
  if(progressError?.status===409){await openStatementHistory(true);return;}
  if(progressError?.status===401){showAuth(false,'请重新登录，再从已上传账单恢复上次保存的进度；刚才未保存的更改没有入账。');return;}
  progressError=null;startProgressSave();
});
function restoreStatement(data) {
  statementGeneration++;currentStatement=data;statementUndo=null;
  progressDirty=0;progressSaved=0;progressError=null;
  statementChoices.clear();statementAppliedCards.clear();statementCardDrafts.clear();
  const saved=data.review_state||{};
  statementPhase=saved.phase||'ownership';
  for(const row of data.rows) {
    const defaults=StatementFlow.choice(row),choice=saved.choices?.[row.row_key];
    if(choice&&!row.processed)for(const key of Object.keys(defaults))if(key in choice)defaults[key]=choice[key];
    statementChoices.set(row.row_key,defaults);
  }
  for(const card of saved.applied_cards||[])statementAppliedCards.add(card);
  for(const [card,draft] of Object.entries(saved.card_drafts||{}))statementCardDrafts.set(card,draft);
  renderStatement(data);
}
async function openStatementHistory(allowStale=false) {
  if(historyBusy||statementBusy)return;
  try {
    if(!allowStale)await flushStatementProgress();
    document.querySelector('#statement-panel').close();
    document.querySelector('#statement-history').showModal();
    historyPage=1;await loadStatementHistory();
  }catch(error){document.querySelector('#statement-error').textContent=error.message;}
}
async function loadStatementHistory() {
  historyBusy=true;
  const list=document.querySelector('#statement-history-items'),errorLine=document.querySelector('#statement-history-error');
  list.replaceChildren();errorLine.textContent='';document.querySelector('#statement-history-status').textContent='正在读取已保存进度…';
  document.querySelector('#statement-history-previous').disabled=true;document.querySelector('#statement-history-next').disabled=true;
  try {
    const response=await fetch(`/api/statements?page=${historyPage}`,{cache:'no-store'});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'无法读取账单列表。');
    historyPage=data.page;historyPages=data.pages;
    for(const item of data.items) {
      const line=el('article',undefined,'statement-history-row');
      const label=item.charge_count?{pending:'待核对',partial:'部分完成',complete:'已完成'}[item.status]:'无需导入';
      line.append(el('h3',`${item.bank==='UOB SG'?'UOB 新加坡':item.bank} · ${item.statement_date}`),el('p',`${label} · ${item.card_count} 张卡 · 已入账 ${item.confirmed_count}/${item.charge_count} 笔消费${item.deferred_count?' · 暂不处理 '+item.deferred_count+' 笔':''}`,'muted'));
      const button=el('button',item.status==='complete'?'查看账单':'继续核对');button.type='button';
      button.addEventListener('click',async()=>{
        if(historyBusy)return;historyBusy=true;button.disabled=true;
        try {
          const preview=await post('/api/statements/resume',{review_id:item.id});
          restoreStatement(preview);document.querySelector('#statement-history').close();
          document.querySelector('#statement-panel').showModal();document.querySelector('#statement-status').textContent='已恢复本机保存的核对进度。';
        }catch(error){errorLine.textContent=error.message;}
        finally{historyBusy=false;button.disabled=false;}
      });line.append(button);list.append(line);
    }
    document.querySelector('#statement-history-status').textContent=data.total?`${data.total} 份账单`:'还没有已上传账单。上传后会自动保存在这里。';
    document.querySelector('#statement-history-page').textContent=`第 ${historyPage} / ${historyPages} 页`;
  }catch(error){errorLine.textContent=error.message;document.querySelector('#statement-history-status').textContent='';}
  finally{historyBusy=false;document.querySelector('#statement-history-previous').disabled=historyPage<=1;document.querySelector('#statement-history-next').disabled=historyPage>=historyPages;}
}
for(const id of ['browse-statements','statement-list'])document.querySelector('#'+id).addEventListener('click',()=>openStatementHistory());
for(const [id,delta] of [['statement-history-previous',-1],['statement-history-next',1]])document.querySelector('#'+id).addEventListener('click',()=>{if(historyBusy)return;historyPage+=delta;loadStatementHistory();});
document.querySelector('#close-statement-history').addEventListener('click',()=>document.querySelector('#statement-history').close());
document.querySelector('#statement-history').addEventListener('close',()=>{if(!document.querySelector('#dashboard').hidden)document.querySelector('#open-statement').focus();});
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
  progressDirty=0;progressSaved=0;progressError=null;showProgressStatus();
  statementChoices.clear();
  statementPhase = 'ownership';
  statementUndo = null;
  statementAppliedCards.clear();statementCardDrafts.clear();
  document.querySelector('#statement-batch').hidden = true;
  document.querySelector('#statement-footer').hidden = true;
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
document.querySelector('#statement-clear').addEventListener('click', async()=>{try{await flushStatementProgress();clearStatementPreview();}catch(error){document.querySelector('#statement-error').textContent=error.message;}});
function showStatementUpload(show) {
  document.querySelector('#statement-upload-controls').hidden=!show;
  document.querySelector('#statement-change').setAttribute('aria-expanded',String(show));
  document.querySelector('#statement-change').textContent=show?'收起上传':'更换账单';
}
document.querySelector('#statement-change').addEventListener('click',async()=>{try{await flushStatementProgress();showStatementUpload(document.querySelector('#statement-upload-controls').hidden);}catch(error){document.querySelector('#statement-error').textContent=error.message;}});
document.querySelector('#statement-file').addEventListener('change', () => {
  statementGeneration++;
  currentStatement = null;
  progressDirty=0;progressSaved=0;progressError=null;showProgressStatus();
  statementChoices.clear();
  statementPhase = 'ownership';
  statementUndo = null;
  statementAppliedCards.clear();statementCardDrafts.clear();
  document.querySelector('#statement-batch').hidden = true;
  document.querySelector('#statement-footer').hidden = true;
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
  document.querySelector('#statement-footer').hidden = true;
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
  feedback.textContent = '正在本机识别账单、核对已有记录并整理分类…';
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
    restoreStatement(data);
    feedback.textContent = '';
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
function getStatementChoice(row) {
  if (!statementChoices.has(row.row_key)) statementChoices.set(row.row_key,StatementFlow.choice(row));
  return statementChoices.get(row.row_key);
}
function statementBlocked() {
  return !!currentStatement?.warnings.length || currentStatement?.cards.some(c=>!c.balance_ok);
}
function rowStage(row) {
  return StatementFlow.stage(row,getStatementChoice(row),statementBlocked());
}
function statementRows(stage) {
  return (currentStatement?.rows || []).filter(row=>rowStage(row)===stage);
}
function statementFeedback(text, focus=true) {
  const node=document.querySelector('#statement-feedback');
  node.textContent=text;
  if(focus) node.focus();
}
function rememberStatementChoices() {
  statementUndo=new Map([...statementChoices].map(([key,value])=>[key,{...value}]));
}
document.querySelector('#statement-undo').addEventListener('click',()=>{
  if(statementBusy || !statementUndo) return;
  statementChoices.clear();
  for(const [key,value] of statementUndo) statementChoices.set(key,value);
  statementUndo=null;renderStatement(currentStatement);statementFeedback('已撤销上次明细调整。卡片长期默认值不受影响。');
});
document.querySelector('#statement-ownership').addEventListener('click',()=>{
  statementPhase='ownership';statementFeedback('',false);renderStatement(currentStatement);
  document.querySelector('#statement-step').focus();
});
function renderStatement(data) {
  showStatementUpload(false);
  document.querySelector('#statement-title').textContent='核对账单';
  document.querySelector('#statement-panel').classList.add('has-preview');
  document.querySelector('#statement-detected').hidden=false;
  document.querySelector('#statement-bank').textContent=`${data.bank==='UOB SG'?'UOB 新加坡':data.bank} · 信用卡账单`;
  document.querySelector('#statement-metadata').textContent=`账单日 ${data.statement_date} · ${data.cards.length} 张卡 · ${data.rows.length} 笔明细`;
  document.querySelector('#statement-batch').hidden=false;
  document.querySelector('#statement-footer').hidden=false;
  document.querySelector('#statement-ownership').hidden=statementPhase==='ownership';
  const step=document.querySelector('#statement-step');
  step.textContent=statementPhase==='ownership'?'1 · 确认卡片归属':'2 · 处理例外';
  step.tabIndex=-1;
  document.querySelector('#statement-step-help').textContent=statementPhase==='ownership'
    ? '卡片设置只决定是否计入你的支出。可整卡应用，再勾回属于你的少数消费；最后统一保存交易。'
    : '先处理待确认项，已就绪明细可展开修改。';
  document.querySelector('#statement-undo').hidden=!statementUndo;
  statementPreview.replaceChildren();
  for(const row of data.rows) getStatementChoice(row);
  for(const warning of data.warnings) statementPreview.append(el('p',warning,'notice'));
  for(const card of data.cards) {
    const rows=data.rows.filter(row=>row.card===card.account);
    const pending=rows.filter(row=>row.kind==='charge'&&!row.processed);
    const cardNode=el('section',undefined,'statement-card');
    const needs=pending.filter(row=>rowStage(row)==='review').length;
    const deferred=pending.filter(row=>rowStage(row)==='deferred').length;
    cardNode.append(el('h3',`${card.name} · ${card.last_four}`),
      el('p',`${rows.length} 笔明细 · ${needs ? needs+' 笔待处理' : deferred ? '待处理已整理' : '无需处理'}${deferred?' · 暂不处理 '+deferred+' 笔':''}`,'muted'));
    const personal=pending.filter(row=>getStatementChoice(row).is_personal).length;
    if(pending.length) cardNode.append(el('p',`未确认消费：${personal} 笔个人 · ${pending.length-personal} 笔非个人`,'personal-status'));
    if(!card.balance_ok) cardNode.append(el('p','余额校验未通过，请先检查账单；本份账单暂不能保存。','notice'));
    if(statementPhase==='ownership') {
      if(pending.length) cardNode.append(cardDefaultForm(data,card));
      else cardNode.append(el('p','本卡没有未确认消费。','muted'));
    } else {
      const sections=[['review','需要处理'],['ready','已就绪'],['deferred','暂不处理'],['processed','已保存'],['other','还款及其他明细']];
      for(const [stage,title] of sections) {
        const subset=rows.filter(row=>rowStage(row)===stage);
        if(!subset.length) continue;
        const group=el('details',undefined,'statement-group');
        group.open=stage==='review';
        group.append(el('summary',`${title} · ${subset.length} 笔`));
        const body=el('div');
        // Mount bounded pages only when opened. Card headings remain visible at every size.
        let limit=50;
        const draw=()=>{
          body.replaceChildren();
          if(stage==='review') renderCategoryGroups(subset,body);
          for(const row of subset.slice(0,limit)) body.append(statementRow(data,row));
          if(subset.length>limit) {
            const more=el('button',`再显示 ${Math.min(50,subset.length-limit)} 笔`);more.type='button';
            more.addEventListener('click',()=>{limit+=50;draw();more.isConnected&&more.focus();});body.append(more);
          }
        };
        let mounted=false;
        const mount=()=>{if(!mounted&&group.open){draw();mounted=true;}};
        group.addEventListener('toggle',mount);group.append(body);mount();cardNode.append(group);
      }
      if(!rows.length) cardNode.append(el('p','本卡本期无明细。','muted'));
    }
    const balance=el('details',undefined,'statement-help-details');
    balance.append(el('summary','余额校验详情'),el('p',`上期余额 ${card.opening_balance??'未识别'} + 本期净变动 ${card.net_movement} = 本期余额 ${card.closing_balance??'未识别'} SGD`,'muted'));
    cardNode.append(balance);statementPreview.append(cardNode);
  }
  statementPreview.hidden=false;
  updateBatchSummary();
  queueStatementProgress();
}
function categorySelect(value='') {
  const label=el('label','分类');
  const select=el('select');select.required=true;
  const blank=el('option','请选择分类');blank.value='';select.append(blank);
  for(const [key,name] of Object.entries(categoryNames)) if(!['transfer','cash_withdrawal'].includes(key)) {
    const option=el('option',name);option.value=key;select.append(option);
  }
  select.value=value;label.append(select);return {label,select};
}
function renderCategoryGroups(rows,parent) {
  const groups=new Map();
  for(const row of rows) {
    const c=getStatementChoice(row);
    if(c.category || !c.is_personal || c.action!=='import' || row.candidate_count ||
      row.possible_duplicate_count || row.intra_statement_duplicate ||
      row.suggestion?.reasons.some(reason=>reason!=='分类需要确认')) continue;
    const key=row.merchant.toUpperCase().trim();
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(row);
  }
  for(const same of groups.values()) if(same.length>1) {
    const form=el('form',undefined,'statement-category-group');
    form.append(el('strong',`${same[0].merchant} · 本卡 ${same.length} 笔待分类`));
    const {label,select}=categorySelect();form.append(label);
    const previous=new Map(same.map(row=>[row.row_key,{...getStatementChoice(row)}]));
    select.addEventListener('change',()=>{
      for(const row of same)Object.assign(getStatementChoice(row),{category:select.value,reviewed:false,dirty:true});
      queueStatementProgress();
    });
    const button=el('button',`应用到这 ${same.length} 笔`);button.type='submit';form.append(button);
    form.addEventListener('submit',event=>{
      event.preventDefault();if(statementBusy)return;rememberStatementChoices();
      for(const [key,value] of previous)statementUndo.set(key,value);
      for(const row of same) Object.assign(getStatementChoice(row),{category:select.value,reviewed:true,dirty:true});
      renderStatement(currentStatement);statementFeedback(`已将本卡这 ${same.length} 笔设为${categoryNames[select.value]}。`);
    });parent.append(form);
  }
}
function cardDefaultForm(data,card) {
  const shell=el('div');
  const controls=el('details',undefined,'statement-group');
  controls.open=!statementAppliedCards.has(card.account);
  controls.append(el('summary',statementAppliedCards.has(card.account)?'整卡归属已应用 · 修改':'设置整卡归属'));
  const form=el('form',undefined,'card-default');
  const pending=data.rows.filter(r=>r.card===card.account&&r.kind==='charge'&&!r.processed);
  const personal=pending.filter(r=>getStatementChoice(r).is_personal).length;
  const label=el('label',undefined,'check-label');
  const check=el('input');check.type='checkbox';
  check.checked=personal===pending.length;check.indeterminate=personal>0&&personal<pending.length;
  const draft=statementCardDrafts.get(card.account);
  if(draft){check.checked=draft.personal;check.indeterminate=false;}
  label.append(check,document.createTextNode('本卡消费默认计入我的支出'));
  const rememberLabel=el('label',undefined,'check-label');
  const remember=el('input');remember.type='checkbox';
  remember.checked=!!draft?.remember;
  const dirty=()=>{statementCardDrafts.set(card.account,{personal:check.checked,remember:remember.checked});updateBatchSummary();queueStatementProgress();};
  check.addEventListener('change',dirty);remember.addEventListener('change',dirty);
  rememberLabel.append(remember,document.createTextNode('记住此设置，下次沿用'));
  const save=el('button','应用到本卡明细');save.type='submit';
  const count=el('p',`当前 ${personal} 笔个人 · ${pending.length-personal} 笔非个人。应用会覆盖本卡全部 ${pending.length} 笔未确认消费的归属。`,'muted');
  form.append(label,count,rememberLabel,save);
  const exceptions=el('details',undefined,'statement-group');
  exceptions.append(el('summary','逐笔调整归属'));
  let mounted=false;
  exceptions.addEventListener('toggle',()=>{
    if(mounted||!exceptions.open)return;mounted=true;
    for(const row of pending) {
      const item=el('label',undefined,'ownership-row');
      const input=el('input');input.type='checkbox';input.checked=getStatementChoice(row).is_personal;
      input.addEventListener('change',()=>{
        if(statementBusy){input.checked=getStatementChoice(row).is_personal;return;}
        rememberStatementChoices();getStatementChoice(row).is_personal=input.checked;getStatementChoice(row).dirty=true;
        const total=pending.filter(r=>getStatementChoice(r).is_personal).length;
        check.checked=total===pending.length;check.indeterminate=total>0&&total<pending.length;
        count.textContent=`当前 ${total} 笔个人 · ${pending.length-total} 笔非个人。应用会覆盖本卡全部 ${pending.length} 笔未确认消费的归属。`;
        shell.closest('.statement-card').querySelector('.personal-status').textContent=`未确认消费：${total} 笔个人 · ${pending.length-total} 笔非个人`;
        document.querySelector('#statement-undo').hidden=false;
        updateBatchSummary();statementFeedback(`已调整 ${row.merchant}：${input.checked?'计入':'不计入'}个人支出。`,false);
        queueStatementProgress();
      });
      item.append(input,el('span',`${row.transaction_date} · ${row.merchant}`),el('span',`${row.amount} SGD`,'amount'));exceptions.append(item);
    }
  });
  controls.append(form);shell.append(controls,exceptions);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(statementBusy)return;
    statementBusy=true;form.setAttribute('aria-busy','true');save.disabled=true;
    try {
      await flushStatementProgress();
      if(remember.checked) {
        const result=await post('/api/statements/card-default',{preview_id:data.preview_id,card:card.account,is_personal:check.checked});
        if(currentStatement!==data)return;
        currentStatement=result.preview;
      }
      rememberStatementChoices();
      StatementFlow.applyCard(data.rows,statementChoices,card.account,check.checked);
      statementAppliedCards.add(card.account);statementCardDrafts.delete(card.account);
      renderStatement(currentStatement);
      statementFeedback(`本卡 ${pending.length} 笔已${check.checked?'计入':'取消计入'}个人支出。${remember.checked?'已记住下次设置。':''}可展开逐笔调整归属。`);
    } catch(error){document.querySelector('#statement-error').textContent=error.message;}
    finally{statementBusy=false;save.disabled=false;form.setAttribute('aria-busy','false');updateBatchSummary();}
  });
  return shell;
}
function statementRow(data,row) {
  const line=el('article',undefined,'statement-row');
  const heading=el('div',undefined,'statement-row-header');
  heading.append(el('strong',row.merchant),el('span',`${money(row.signed_amount)} SGD`,'amount'));
  line.append(heading,el('p',`消费 ${row.transaction_date} · 入账 ${row.posting_date}`,'muted'));
  if(row.correction_history?.length) {
    const history=el('details',undefined,'statement-help-details');
    history.append(el('summary',`更正历史 · ${row.correction_count} 次`));
    for(const item of row.correction_history) history.append(el('p',`${new Date(item.created_at).toLocaleString('zh-CN')} · ${item.operation==='unlink'?'解除错误关联，保留原记账':'撤销补录，保留历史'} · 记录 #${item.transaction_id}`,'muted'));
    if(row.correction_count>row.correction_history.length)history.append(el('p','显示最近 5 次；完整历史保存在本机。','muted'));
    line.append(history);
  }
  if(row.processed) {
    line.append(el('p',`已${row.processed.action==='match'?'关联':'新增'} #${row.processed.transaction_id} · ${row.processed.is_personal?'个人':'非个人'}支出`,'statement-processed'));
    const corrections=el('details',undefined,'statement-help-details');corrections.append(el('summary','这笔处理有误？'));
    corrections.append(el('p','只改分类或个人归属，可在账本交易详情修改。','muted'));
    const button=el('button',row.processed.action==='match'?'解除错误关联':'撤销这笔补录','quiet');button.type='button';
    button.addEventListener('click',async()=>{
      if(statementBusy)return;
      try {
        await flushStatementProgress();
        const saved=row.processed,unlink=saved.action==='match';
        statementSelection={data:currentStatement,reversal:true,button,body:{preview_id:currentStatement.preview_id,row_key:row.row_key,
          transaction_id:saved.transaction_id,link_id:saved.link_id,revision:saved.revision,operation:unlink?'unlink':'void_import'}};
        document.querySelector('#statement-confirm-title').textContent=unlink?'解除这笔关联？':'撤销这笔补录？';
        document.querySelector('#statement-confirm-save').textContent=unlink?'确认解除关联':'确认撤销补录';
        document.querySelector('#statement-confirm-description').textContent=`银行明细：${row.merchant}\n${row.amount} SGD · ${row.transaction_date}\n账本记录 #${saved.transaction_id}：${saved.merchant}\n\n${unlink?'只解除账单与这笔记账的关联。原记账保留，支出金额不变；当前分类和个人归属也保留，可在交易详情修改。':`这笔由账单新增的记账将标记为已撤销，退出最近记录。${saved.is_personal?'个人支出将减少 '+row.amount+' SGD。':'原为非个人支出，个人金额不变。'}原记录保留，不永久删除。`}\n\n更正历史会保存。这条账单明细会回到待处理，重新核对后可再次入账。`;
        document.querySelector('#statement-confirm-duplicate').hidden=true;
        document.querySelector('#statement-confirm-error').textContent='';
        document.querySelector('#statement-confirm').showModal();
      }catch(error){document.querySelector('#statement-error').textContent=error.message;}
    });corrections.append(button);line.append(corrections);return line;
  }
  if(row.kind!=='charge') {
    line.append(el('p',row.kind==='payment'?'信用卡还款，不重复计作消费。':'贷记／退款，暂不导入；仍需核对原消费。','muted'));return line;
  }
  const c=getStatementChoice(row);
  const adjustment=el('details',undefined,'statement-adjust');
  adjustment.open=rowStage(row)==='review';
  adjustment.append(el('summary',`${c.action==='import'?'新增':c.action?'关联 #'+c.action:'匹配待确认'} · ${categoryNames[c.category]||(!c.is_personal?'未分类':'分类待确认')} · ${c.is_personal?'个人':'非个人'} · 修改`));
  adjustment.append(statementReviewForm(data,row));
  line.append(adjustment);return line;
}
function statementReviewForm(data,row) {
  const review=el('form',undefined,'statement-review');
  review.setAttribute('aria-label',`处理 ${row.merchant} ${row.amount} SGD`);
  const c=getStatementChoice(row);
  const beforeEdit={...c};
  const reasons=row.suggestion?.reasons||[];
  if(reasons.length && rowStage(row)==='review') review.append(el('p',reasons.filter(r=>c.is_personal||r!=='分类需要确认').join('；'),'muted'));
  const actionLabel=el('label','入账方式');const action=el('select');action.required=true;
  for(const [value,text] of [['','请选择'],['import','作为新交易'],...row.candidates.map(c=>[String(c.id),`关联 #${c.id} · ${c.merchant} · ${c.transaction_date} · ${c.account}`])]) {
    const option=el('option',text);option.value=value;action.append(option);
  }
  action.value=c.action;actionLabel.append(action);
  if(row.candidate_count) review.append(actionLabel);else {action.value='import';review.append(el('p','将作为新交易保存。','muted'));}
  const {label,select}=categorySelect(c.category);
  select.required=c.is_personal;review.append(label);
  const personalLabel=el('label',undefined,'check-label');
  const personal=el('input');personal.type='checkbox';personal.checked=c.is_personal;
  personalLabel.append(personal,document.createTextNode('计入我的个人支出'));review.append(personalLabel);
  const duplicateLabel=el('label',undefined,'check-label');
  const duplicate=el('input');duplicate.type='checkbox';duplicate.checked=c.acknowledge;
  duplicateLabel.append(duplicate,document.createTextNode('已核对，这是另一笔消费，不是重复记录'));review.append(duplicateLabel);
  const recheckLabel=el('label',undefined,'check-label');
  const recheck=el('input');recheck.type='checkbox';recheck.checked=!!c.rechecked;recheck.required=!!row.requires_recheck;
  recheckLabel.append(recheck,document.createTextNode('我已重新核对这笔曾更正的明细'));recheckLabel.hidden=!row.requires_recheck;review.append(recheckLabel);
  const syncDuplicate=()=>{duplicateLabel.hidden=action.value!=='import'||!(row.possible_duplicate_count||row.intra_statement_duplicate);duplicate.required=!duplicateLabel.hidden;};
  action.addEventListener('change',()=>{
    const candidate=row.candidates.find(candidate=>String(candidate.id)===action.value);
    if(candidate)select.value=candidate.category;
    syncDuplicate();
    draftChanged();
  });syncDuplicate();
  const draftChanged=()=>{
    Object.assign(c,{action:action.value,category:select.value,is_personal:personal.checked,acknowledge:duplicate.checked,rechecked:recheck.checked,reviewed:false,dirty:true});
    queueStatementProgress();
  };
  select.addEventListener('change',draftChanged);duplicate.addEventListener('change',draftChanged);
  recheck.addEventListener('change',draftChanged);
  personal.addEventListener('change',()=>{select.required=personal.checked;draftChanged();});
  review.append(el('p','非个人消费可留空分类，保存时归入「其他」。这里只整理，最后统一保存。','muted'));
  const actions=el('div',undefined,'page-actions');
  const done=el('button',rowStage(row)==='review'?'完成此项':'应用修改');done.type='submit';
  const defer=el('button',c.deferred?'恢复处理':'暂不处理','quiet');defer.type='button';
  defer.addEventListener('click',()=>{
    if(statementBusy)return;rememberStatementChoices();c.deferred=!c.deferred;c.dirty=true;
    renderStatement(currentStatement);statementFeedback(c.deferred?'已移至暂不处理，不会加入本次保存。':'已恢复处理。');
  });actions.append(done,defer);review.append(actions);
  review.addEventListener('submit',event=>{
    event.preventDefault();if(statementBusy)return;
    const next={...c,action:action.value,category:select.value,is_personal:personal.checked,acknowledge:duplicate.checked,rechecked:recheck.checked,reviewed:true,deferred:false,dirty:true};
    if(StatementFlow.stage(row,next,statementBlocked())!=='ready') {
      document.querySelector('#statement-error').textContent='此项仍有异常：请检查金额、分类或重复确认；也可以暂不处理。';return;
    }
    rememberStatementChoices();statementUndo.set(row.row_key,beforeEdit);Object.assign(c,next);document.querySelector('#statement-error').textContent='';
    renderStatement(currentStatement);statementFeedback('已完成此项，移至已就绪。');
  });return review;
}
function formatBatchMoney(rows) {
  const cents=rows.reduce((total,row)=>total+BigInt(row.amount.replace('.','')),0n);
  return `${(cents/100n).toLocaleString('en-SG')}.${(cents%100n).toString().padStart(2,'0')}`;
}
function updateBatchSummary() {
  if(!currentStatement)return;
  const rows=statementRows('ready'),review=statementRows('review'),deferred=statementRows('deferred');
  const personal=rows.filter(r=>getStatementChoice(r).is_personal);
  document.querySelector('#statement-batch-summary').textContent=`已就绪 ${rows.length} 笔 · 个人 ${formatBatchMoney(personal)} SGD · 非个人 ${rows.length-personal.length} 笔${deferred.length?' · 暂不处理 '+deferred.length+' 笔':''}`;
  const button=document.querySelector('#statement-confirm-batch');
  button.textContent=statementCardDrafts.size?'请先应用卡片归属':statementPhase==='ownership'?'检查待处理':review.length?`查看 ${review.length} 笔待处理`:rows.length?(deferred.length?'核对并保存已就绪记录':'核对并保存'):'暂无待保存记录';
  button.disabled=statementBusy||statementCardDrafts.size>0||statementBlocked()||(statementPhase!=='ownership'&&!review.length&&!rows.length);
}
function rowConfirmationBody(row) {
  const c=getStatementChoice(row);
  if(rowStage(row)!=='ready')throw new Error(`${row.merchant}：尚未完成处理。`);
  const candidate=row.candidates.find(item=>String(item.id)===c.action);
  return {row_key:row.row_key,action:c.action==='import'?'import':'match',category:StatementFlow.category(c),is_personal:c.is_personal,
    transaction_id:candidate?.id,revision:candidate?.revision,acknowledge_possible_duplicate:c.acknowledge,acknowledge_correction:!!c.rechecked};
}
document.querySelector('#statement-confirm-batch').addEventListener('click',async()=>{
  if(statementBusy||!currentStatement||statementCardDrafts.size)return;
  try{await flushStatementProgress();}catch(error){document.querySelector('#statement-error').textContent=error.message;return;}
  if(statementPhase==='ownership'){
    statementPhase='review';statementFeedback('',false);renderStatement(currentStatement);document.querySelector('#statement-step').focus();return;
  }
  const review=statementRows('review');
  if(review.length){
    const first=statementPreview.querySelector('.statement-group[open] select, .statement-group[open] button');
    first?.focus();statementFeedback(`还有 ${review.length} 笔待处理。完成或设为暂不处理后继续。`,false);return;
  }
  try {
    if(statementBlocked())throw new Error('账单校验未通过，不能保存。');
    const rows=statementRows('ready');if(!rows.length)return;
    const items=rows.map(rowConfirmationBody);
    const targets=items.filter(item=>item.action==='match').map(item=>item.transaction_id);
    if(new Set(targets).size!==targets.length)throw new Error('多行选择了同一笔记账，请调整关联或暂不处理其中一笔。');
    const imports=items.filter(item=>item.action==='import').length;
    const personal=rows.filter(row=>getStatementChoice(row).is_personal);
    const categories={};
    for(const row of rows){const c=getStatementChoice(row);const name=categoryNames[c.category]||'其他（非个人未分类）';categories[name]=(categories[name]||0)+1;}
    statementSelection={data:currentStatement,batch:true,button:document.querySelector('#statement-confirm-batch'),body:{preview_id:currentStatement.preview_id,items}};
    document.querySelector('#statement-confirm-title').textContent='核对本次保存';
    document.querySelector('#statement-confirm-save').textContent=`确认保存 ${rows.length} 笔`;
    document.querySelector('#statement-confirm-description').textContent=`关联已有 ${rows.length-imports} 笔，不重复新增\n新增 ${imports} 笔\n个人支出 ${personal.length} 笔 · ${formatBatchMoney(personal)} SGD\n非个人支出 ${rows.length-personal.length} 笔 · ${formatBatchMoney(rows.filter(r=>!getStatementChoice(r).is_personal))} SGD\n分类：${Object.entries(categories).map(([name,count])=>name+' '+count+' 笔').join('、')}\n\n暂不处理 ${statementRows('deferred').length} 笔，本次不保存；还款及贷记不导入。\n如有冲突，整批不保存。`;
    document.querySelector('#statement-confirm-duplicate').hidden=true;
    document.querySelector('#statement-confirm-error').textContent='';
    document.querySelector('#statement-error').textContent='';
    document.querySelector('#statement-confirm').showModal();
  }catch(error){document.querySelector('#statement-error').textContent=error.message;}
});
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
    await flushStatementProgress();
    selection.body.preview_id=selection.data.preview_id;
    const result = await post(selection.reversal?'/api/statements/correct':selection.batch ? '/api/statements/confirm-batch' : '/api/statements/confirm', selection.body);
    if (currentStatement !== selection.data) return;
    if(selection.reversal) {
      statementFeedback('',false);
      const row=result.preview.rows.find(item=>item.row_key===selection.body.row_key);
      const choice=StatementFlow.choice(row);
      const saved=result.preview.review_state?.choices?.[row.row_key];
      if(saved){choice.category=saved.category;choice.is_personal=saved.is_personal;}
      choice.reviewed=false;choice.rechecked=false;choice.acknowledge=false;choice.deferred=false;
      statementChoices.set(row.row_key,choice);
      statementPhase='review';
    }
    currentStatement = result.preview;
    statementUndo = null;
    dialog.close();
    renderStatement(currentStatement);
    document.querySelector('#statement-status').textContent = selection.reversal ? (result.operation==='unlink'?'已解除关联，原记账保留、支出金额不变。':'已撤销补录，原记录保留历史，已退出支出统计。')+' 账单明细已回到待处理。' : selection.batch ? `整批 ${result.count} 笔已保存。匹配不重复增加交易，非个人消费不计入个人统计。` : `已${result.action === 'match' ? '匹配' : '补录'}记录 #${result.transaction_id} · ${result.is_personal ? '计入个人支出' : '非个人支出，不计入统计'}。`;
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
