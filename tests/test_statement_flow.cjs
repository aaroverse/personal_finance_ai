// 명세서의 귀속 전파와 예외 검토 상태 전이를 검증합니다.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const flow=require('../src/finance_bot/static/statement-flow.js');
const row=(extra={})=>({row_key:'a',card:'UOB 4444',kind:'charge',amount:'12.00',processed:null,
  candidates:[],possible_duplicate_count:0,suggestion:{action:'import',category:'dining',is_personal:true,reasons:[]},...extra});
test('known charges are ready, personal unknown categories need review',()=>{
  const r=row();assert.equal(flow.stage(r,flow.choice(r)),'ready');
  r.suggestion.category=null;r.suggestion.reasons=['分类需要确认'];
  assert.equal(flow.stage(r,flow.choice(r)),'review');
});
test('non-personal unknown category uses other without waiving duplicate checks',()=>{
  const r=row({suggestion:{action:'import',category:null,is_personal:false,reasons:['分类需要确认']}});
  const c=flow.choice(r);assert.equal(flow.category(c),'other');assert.equal(flow.stage(r,c),'ready');
  r.possible_duplicate_count=1;assert.equal(flow.stage(r,c),'review');
  c.acknowledge=true;assert.equal(flow.stage(r,c),'ready');
});
test('card flag applies across all pending rows, preserves other cards and processed rows',()=>{
  const rows=[row(),row({row_key:'b'}),row({row_key:'c',card:'UOB 5555'}),row({row_key:'d',processed:{}})];
  const choices=new Map(rows.map(r=>[r.row_key,flow.choice(r)]));
  flow.applyCard(rows,choices,'UOB 4444',false);
  assert.deepEqual([...choices.values()].map(c=>c.is_personal),[false,false,true,true]);
  choices.get('b').is_personal=true;
  assert.equal(choices.get('a').is_personal,false);assert.equal(choices.get('b').is_personal,true);
  flow.applyCard(rows,choices,'UOB 4444',true);
  assert.ok([...choices.values()].every(c=>c.is_personal));
});
test('date/card ambiguity requires explicit review even when category and action exist',()=>{
  const r=row({suggestion:{action:'match',transaction_id:7,category:'dining',is_personal:true,reasons:['消费日期不完全一致']},candidates:[{id:7}]});
  const c=flow.choice(r);assert.equal(flow.stage(r,c),'review');
  c.reviewed=true;assert.equal(flow.stage(r,c),'ready');
  c.action='8';assert.equal(flow.stage(r,c),'review');
});
test('intra-statement duplicates require explicit acknowledgement',()=>{
  const r=row({intra_statement_duplicate:true}),c=flow.choice(r);c.reviewed=true;
  assert.equal(flow.stage(r,c),'review');c.acknowledge=true;assert.equal(flow.stage(r,c),'ready');
});
test('zero amounts and failed balances never become ready',()=>{
  const r=row(),c=flow.choice(r);c.reviewed=true;
  assert.equal(flow.stage(r,c,true),'review');r.amount='0.00';assert.equal(flow.stage(r,c),'review');
});
test('defer, restore and saved states stay separate from personal attribution',()=>{
  const r=row(),c=flow.choice(r);c.deferred=true;c.is_personal=false;
  assert.equal(flow.stage(r,c),'deferred');c.deferred=false;assert.equal(flow.stage(r,c),'ready');
  r.processed={};assert.equal(flow.stage(r,c),'processed');
});
test('payments and credits cannot enter expense saving',()=>{
  for(const kind of ['payment','credit']) {const r=row({kind});assert.equal(flow.stage(r,flow.choice(r)),'other');}
});
