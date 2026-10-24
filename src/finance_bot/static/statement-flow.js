// 카드 귀속과 명세서 검토 상태를 결정하는 순수 함수를 제공합니다.
const StatementFlow = {
  choice(row) {
    const s = row.suggestion || {};
    return {action:s.action === 'match' ? String(s.transaction_id) : (s.action || ''),
      category:s.category || '', is_personal:s.is_personal ?? true, acknowledge:false,
      reviewed:false, deferred:false, dirty:false};
  },
  category(choice) { return choice.category || (!choice.is_personal ? 'other' : ''); },
  stage(row, choice, blocked=false) {
    if (row.processed) return 'processed';
    if (row.kind !== 'charge') return 'other';
    if (choice.deferred) return 'deferred';
    if (blocked || Number(row.amount) <= 0) return 'review';
    if (!this.category(choice) || !choice.action) return 'review';
    if (choice.action !== 'import' && !row.candidates.some(c => String(c.id) === choice.action)) return 'review';
    if (choice.action === 'import' && (row.possible_duplicate_count || row.intra_statement_duplicate) && !choice.acknowledge) return 'review';
    const reasons = (row.suggestion?.reasons || []).filter(r => r !== '分类需要确认');
    return reasons.length && !choice.reviewed ? 'review' : 'ready';
  },
  applyCard(rows, choices, card, personal) {
    for (const row of rows) if (row.card === card && row.kind === 'charge' && !row.processed) {
      const choice = choices.get(row.row_key);
      choice.is_personal = personal;
      choice.dirty = true;
    }
  }
};
if (typeof module !== 'undefined') module.exports = StatementFlow;
