// 기본 장부의 명시적인 UI 번역과 언어 설정을 관리합니다.
(function (root) {
  const english = {
  "我的账本 · Personal Finance": "My ledger · Personal Finance",
  "私人账本": "Private ledger",
  "我的账本": "My ledger",
  "登录账本": "Log in to your ledger",
  "页面密码": "Page password",
  "再次输入密码": "Confirm password",
  "登录": "Log in",
  "本月已确认消费": "Confirmed spending this month",
  "上传账单": "Upload statement",
  "刷新": "Refresh",
  "退出": "Log out",
  "正在读取账本…": "Loading your ledger…",
  "已确认个人消费汇总": "Confirmed personal spending",
  "统计范围": "What is included",
  "仅汇总计入个人支出的已确认消费。非个人记录、待确认、转账和取现不计入；流水仍保留。": "Totals include confirmed personal spending only. Non-personal records, drafts, transfers and cash withdrawals are excluded; their records are retained.",
  "筛选账本": "Filter ledger",
  "月份": "Month",
  "搜索": "Search",
  "商户或备注": "Merchant or note",
  "应用": "Apply",
  "更多筛选": "More filters",
  "付款账户": "Payment account",
  "全部账户": "All accounts",
  "币种": "Currency",
  "全部币种": "All currencies",
  "分类": "Category",
  "全部分类": "All categories",
  "清除筛选": "Clear filters",
  "交易记录": "Transactions",
  "记录状态": "Record status",
  "已确认": "Confirmed",
  "待确认": "Pending",
  "已删除": "Deleted",
  "点击一笔草稿，检查后确认入账或取消。": "Open a draft to review, confirm or cancel it.",
  "交易": "Transaction",
  "分类与账户": "Category & account",
  "金额": "Amount",
  "上一页": "Previous",
  "下一页": "Next",
  "分类支出": "Spending by category",
  "交易详情": "Transaction details",
  "关闭详情": "Close details",
  "关闭 ×": "Close ×",
  "商户或消费描述": "Merchant or description",
  "消费日期": "Transaction date",
  "例如 SG UOB 信用卡": "e.g. SG UOB credit card",
  "备注": "Note",
  "计入我的个人支出": "Include in my personal spending",
  "取消后保留记录用于对账，但不计入个人支出及分类统计。": "Uncheck to retain the record while excluding it from personal totals and category spending.",
  "原始来源描述（保留不变）": "Original description (preserved)",
  "保存修改": "Save changes",
  "返回详情": "Back to details",
  "修改记录": "Edit record",
  "删除记录": "Delete record",
  "恢复记录": "Restore record",
  "确认入账": "Confirm transaction",
  "取消草稿": "Cancel draft",
  "确定": "Confirm",
  "返回": "Back",
  "修改会保留原始描述并记录历史。保存草稿后，仍需确认入账。删除记录可在「已删除」中恢复。": "Edits preserve the original description and are recorded in history. Saved drafts still need confirmation. Deleted records can be restored from Deleted.",
  "正在更新账本…": "Updating your ledger…",
  "登录已过期，请重新登录。": "Your session expired. Please log in again.",
  "无法读取账本": "Unable to load your ledger",
  "未注明账户": "Unspecified account",
  "全部月份的个人支出": "Personal spending across all months",
  "新加坡元": "Singapore dollars",
  "马来西亚令吉": "Malaysian ringgit",
  "已删除记录不计入统计。打开一笔记录可恢复。": "Deleted records are excluded from totals. Open a record to restore it.",
  "非个人支出": "Non-personal",
  "已删除 · 可恢复": "Deleted · restorable",
  "暂无待确认记录": "No pending records",
  "暂无符合条件的记录": "No matching records",
  "调整筛选，或到 Telegram 添加记录。": "Adjust your filters or add a record in Telegram.",
  "当前范围还没有已确认消费。": "No confirmed spending in this range yet.",
  "未注明": "Unspecified",
  "状态": "Status",
  "已删除 · 不计入统计": "Deleted · excluded from totals",
  "待确认 · 尚未入账": "Pending · not yet recorded",
  "个人支出": "Personal spending",
  "计入": "Included",
  "不计入（保留对账记录）": "Excluded (record retained)",
  "原始描述": "Original description",
  "识别提示": "Extraction notes",
  "删除时间": "Deleted at",
  "银行商户": "Bank merchant",
  "账单卡片": "Statement card",
  "银行入账日": "Posting date",
  "账单日期": "Statement date",
  "设置账本密码": "Set a ledger password",
  "首次使用，请设置至少 12 个字符的页面密码。密码仅保存在本机，不是银行或 Telegram 密码。": "Set a page password with at least 12 characters. It is stored locally. Use a separate password from your bank and Telegram.",
  "输入本机页面密码，查看和管理账本。": "Enter your page password to view and manage your ledger.",
  "设置并进入": "Set password & continue",
  "登录已过期，操作未完成。请重新登录后核对。": "Your session expired before this action completed. Log in again and check the record.",
  "操作失败，请重试。": "Action failed. Please try again.",
  "两次输入的密码不一致。": "The passwords do not match.",
  "草稿修改已保存，仍需确认入账。": "Draft changes saved. Confirmation is still required.",
  "修改已保存。": "Changes saved.",
  "金额需大于零，且最多两位小数。": "Enter an amount greater than zero with up to two decimal places.",
  "请检查此字段的格式与长度。": "Check this field’s format and length.",
  "确认这笔金额、分类和账户正确，并写入账本？": "Confirm this amount, category and account and add it to your ledger?",
  "取消这份草稿？取消后不会计入支出。": "Cancel this draft? It will not count toward spending.",
  "恢复后重新计入个人支出。": "It will count toward personal spending again.",
  "此笔为非个人支出，个人统计金额不变。": "This is a non-personal record. Personal totals will stay the same.",
  "确定取消草稿": "Confirm cancellation",
  "确认删除": "Confirm deletion",
  "确认恢复": "Confirm restoration",
  "已确认入账。": "Transaction confirmed.",
  "草稿已取消。": "Draft cancelled.",
  "已移到已删除，不计入统计；可恢复。": "Moved to Deleted and excluded from totals. You can restore it.",
  "记录已恢复。": "Record restored.",
  "无法连接账本服务，请确认服务正在运行后刷新页面。": "Unable to connect to your ledger. Check that the service is running, then refresh.",
  "请输入有效密码。": "Enter a valid password.",
  "尝试次数过多，请一分钟后再试。": "Too many attempts. Try again in one minute.",
  "密码已经设置，请直接登录。": "A password is already set. Please log in.",
  "密码至少需要 12 个字符。": "Your password must contain at least 12 characters.",
  "请先设置本机页面密码。": "Set your local page password first.",
  "密码不正确，请重试。": "Incorrect password. Please try again.",
  "请求内容无效。": "Invalid request.",
  "无法读取或保存页面登录设置。": "Unable to read or save login settings.",
  "安全凭证已失效，请刷新页面。": "Your security token expired. Please refresh the page.",
  "操作不存在。": "This action does not exist.",
  "记录版本无效，请刷新后重试。": "Invalid record version. Refresh and try again.",
  "已确认交易不能再次审核。": "Confirmed transactions cannot be reviewed again.",
  "请检查标记的字段：金额需大于零且最多两位小数，其他字段需完整。": "Check the marked fields. Amounts must be positive with up to two decimal places; other required fields must be complete.",
  "记录已更新、已撤销或已关联账单。请关闭并刷新后重试。仍关联账单的金额、日期、币种与账户不可改动；分类和个人支出可在有效记录中修改。": "This record has changed, was removed or is linked to a statement. Close and refresh before retrying. Linked amounts, dates, currencies and accounts cannot be edited; categories and personal spending can be changed on active records.",
  "请填写完整的交易资料。": "Complete the transaction details.",
  "暂时无法保存，请稍后重试。": "Unable to save right now. Please try again later.",
  "暂时无法读取账本，请检查数据库路径或稍后刷新。": "Unable to read the ledger right now. Check the database path or refresh later.",
  "餐饮": "Dining",
  "日用品与买菜": "Groceries & essentials",
  "交通": "Transport",
  "购物": "Shopping",
  "账单": "Bills",
  "住房": "Housing",
  "健康": "Health",
  "教育": "Education",
  "旅行": "Travel",
  "娱乐": "Entertainment",
  "取现": "Cash withdrawal",
  "转账": "Transfer",
  "其他": "Other",
  "已确认 ${}": "Confirmed ${}",
  "待确认 ${}": "Pending ${}",
  "已删除 ${}": "Deleted ${}",
  "${} 笔记录": "${} records",
  "更多筛选 (${})": "More filters (${})",
  "第 ${} / ${} 页": "Page ${} / ${}",
  "更新于 ${}": "Updated at ${}",
  "读取失败：${}。请点“刷新账本”重试。": "Unable to load: ${}. Select Refresh to try again.",
  "${} 年 ${} 月个人支出": "Personal spending · ${}-${}",
  "删除「${}」${} ${}？移至已删除后不计入统计，可随时恢复。": "Delete “${}” (${} ${})? It will be excluded from totals and can be restored anytime.",
  "恢复「${}」${} ${}？${}": "Restore “${}” (${} ${})? ${}",
  "请求来源不正确，请重新打开页面。": "The request origin is invalid. Reopen the page.",
  "需要 JSON 请求。": "This request must use JSON.",
  "请先登录账本。": "Please log in first.",
  "访问地址或请求来源未获准。": "This address or request origin is not allowed.",
  "只允许你的 Tailscale 账号访问。": "Access is limited to your Tailscale account.",
  "请先在 MBP 本机设置账本密码。": "Set your ledger password locally on your MacBook first.",
  "筛选条件无效。": "Invalid filters"
};
  const chinese = Object.fromEntries(Object.entries(english).map(([zh, en]) => [en, zh]));
  const storageKey = 'finance-language';
  let language = 'zh-Hans';
  try { if (root.localStorage?.getItem(storageKey) === 'en') language = 'en'; } catch (_) { /* Private browsing may block storage. */ }
  function setLanguage(value) {
    language = value === 'en' ? 'en' : 'zh-Hans';
    try { root.localStorage?.setItem(storageKey, language); } catch (_) { /* Switching still works for this visit. */ }
  }
  function t(text, ...values) {
    const template = Array.isArray(text);
    const key = template ? text.join('${}') : text;
    const canonical = Object.hasOwn(chinese, key) ? chinese[key] : key;
    const translated = language === 'en' && Object.hasOwn(english, canonical) ? english[canonical] : canonical;
    if (!template) return translated;
    let index = 0;
    return translated.replace(/\$\{\}/g, () => String(values[index++]));
  }
  function locale() { return language === 'en' ? 'en-SG' : 'zh-CN'; }
  function applyLanguage() {
    root.document.documentElement.lang = language;
    for (const attr of ['text', 'placeholder', 'aria-label']) {
      const name = attr === 'text' ? 'data-i18n' : `data-i18n-${attr}`;
      for (const node of root.document.querySelectorAll(`[${name}]`)) {
        const value = t(node.getAttribute(name));
        if (attr === 'text') node.textContent = value;
        else node.setAttribute(attr, value);
      }
    }
    root.document.getElementById('language').value = language;
  }
  const api = {t, locale, setLanguage, applyLanguage};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FinanceI18n = api;
})(typeof window === 'undefined' ? globalThis : window);
