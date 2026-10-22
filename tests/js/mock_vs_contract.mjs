import fs from 'node:fs';
import assert from 'node:assert/strict';
const ROOT = process.argv[2];
const md = fs.readFileSync(`${ROOT}/_workspace/01_architect_contracts.md`, 'utf8');
const html = fs.readFileSync(`${ROOT}/web_chat.html`, 'utf8');

// 계약 예시 (a)~(g) JSON 블록 추출
const ex = {};
for (const k of 'abcdefg') {
  const i = md.indexOf(`**(${k})`);
  const s = md.indexOf('```json', i) + 7, e = md.indexOf('```', s);
  ex[k] = JSON.parse(md.slice(s, e));
}
// (h) 는 주석 포함 → JSON 줄만
const hi = md.indexOf('**(h)'); const hs = md.indexOf('```json', hi) + 7;
const hLines = md.slice(hs, md.indexOf('```', hs)).split('\n').filter(l => l.trim().startsWith('{')).map(l => JSON.parse(l));

// MockApi IIFE 만 잘라 실행
const s = html.indexOf('const PARAMS'); const e = html.indexOf('})();', html.indexOf('const MockApi')) + 5;
const src = html.slice(s, e);
function load(search = '?mock=1') {
  const location = { search, origin: 'http://localhost:8701' };
  return new Function('location', 'URLSearchParams', 'URL', src + '\nreturn MockApi;')(location, URLSearchParams, URL);
}
const stream = (api, msg, uid = 'u-3f9a2c1d') => new Promise(res => {
  const frames = []; const es = new api.EventSource(`/chat/stream?message=${encodeURIComponent(msg)}&user_id=${uid}`);
  es.onmessage = ({ data }) => { frames.push(data === '[DONE]' ? data : JSON.parse(data)); if (data === '[DONE]') res(frames); };
});
const CASE1 = '어제 술 먹고 치킨까지 먹어서 속이 안 좋아. 기름진 건 못 먹겠고 혼자 먹을 건데 만원 안쪽으로 20분 안에 되는 거 없을까';
const CASE1B = '오늘도 속이 별로야. 어제처럼 만원 안쪽으로 20분 안에 먹을 수 있는 걸로 추천해 줘';
const CASE3 = '돈이 없어서 5000원 안쪽으로 먹어야 해. 근데 밀가루 알레르기 있어서 면이랑 빵은 못 먹어';
const TOP = Object.keys(ex.a), META = Object.keys(ex.a.metadata);
const results = [];
const check = (name, fn) => Promise.resolve().then(fn).then(() => results.push(['PASS', name]), err => results.push(['FAIL', name, err.message.split('\n').slice(0, 12).join('\n')]));
function shape(r) {
  assert.deepEqual(Object.keys(r).sort(), [...TOP].sort(), 'ChatResponse 키 집합');
  assert.deepEqual(Object.keys(r.metadata).sort(), [...META].sort(), 'metadata 키 집합');
}
function sse(frames) {
  assert.equal(frames.at(-1), '[DONE]'); const fa = frames.at(-2);
  assert.equal(fa.type, 'FinalAnswer'); assert.equal(fa.reply, fa.response.reply); assert.equal(fa.prompt, fa.response.prompt);
  const evs = frames.filter(f => typeof f === 'object' && !['FinalAnswer', 'AnswerToken'].includes(f.type));
  assert.deepEqual(evs, fa.response.events, 'SSE 이벤트 순서 = response.events');
  for (const f of frames) if (typeof f === 'object' && f.type !== 'AnswerToken' && f.type !== 'FinalAnswer') assert.ok(f.node, `node 누락 ${f.type}`);
  return fa.response;
}
await check('(a) case1 done == 계약 예시 a', async () => { const r = sse(await stream(load(), CASE1)); shape(r); assert.deepEqual(r, ex.a); });
await check('(b) case3 needs_approval == 예시 b', async () => { const r = sse(await stream(load(), CASE3)); shape(r); assert.deepEqual(r, ex.b); });
await check('(c) resume approved == 예시 c', async () => { const api = load(); await stream(api, CASE3);
  const res = await api.fetch('/chat/resume', { method: 'POST', body: JSON.stringify({ user_id: 'u-3f9a2c1d', approved: true }) });
  assert.equal(res.status, 200); shape(res.body); assert.deepEqual(res.body, ex.c); });
await check('(d) resume rejected == 예시 d', async () => { const api = load(); await stream(api, CASE3);
  const res = await api.fetch('/chat/resume', { method: 'POST', body: JSON.stringify({ user_id: 'u-3f9a2c1d', approved: false }) });
  shape(res.body); assert.deepEqual(res.body, ex.d); });
await check('(e) greeting chat == 예시 e (+AnswerToken 합=reply)', async () => { const f = await stream(load(), '안녕하세요. 어떤 일을 할 수 있나요?');
  const r = sse(f); shape(r); assert.deepEqual(r, ex.e);
  assert.equal(f.filter(x => x.type === 'AnswerToken').map(x => x.delta).join(''), r.reply); });
await check('(f) bad_reason kept/review_flags ⊇ 예시 f', async () => { const r = sse(await stream(load('?mock=1&mock_mode=bad_reason'), CASE1));
  assert.deepEqual(r.kept.find(k => k.name === '북엇국'), ex.f.kept[0]); assert.deepEqual(r.review_flags, ex.f.review_flags);
  assert.deepEqual(r.events.find(x => x.type === 'Reviewed').flagged, ['북엇국']); });
await check('(g) case1 → 먹었어요 된장찌개 → case1b excluded == 예시 g', async () => { const api = load(); await stream(api, CASE1);
  const m = await api.fetch('/meals', { method: 'POST', body: JSON.stringify({ user_id: 'u-3f9a2c1d', menu_name: '된장찌개' }) });
  assert.equal(m.status, 200); assert.deepEqual(Object.keys(m.body).sort(), ['item', 'ok']); assert.deepEqual(Object.keys(m.body.item).sort(), ['eaten_at', 'id', 'menu_name']);
  assert.match(m.body.item.eaten_at, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/);
  const r = sse(await stream(api, CASE1B)); assert.deepEqual(r.excluded, ex.g.excluded);
  const g = await api.fetch('/meals?user_id=u-3f9a2c1d&days=7'); assert.deepEqual(Object.keys(g.body).sort(), ['days', 'items', 'user_id']); });
await check('(h1) 공백 메시지 → SSE Error 400 EMPTY_MESSAGE == 예시 h', async () => { const f = await stream(load(), '   ');
  assert.equal(f.length, 2); assert.equal(f[0].type, 'Error'); assert.equal(f[0].status_code, 400); assert.deepEqual({ detail: f[0].detail }, hLines[0]); });
await check('(h2) 대기 없는 resume → 409 == 예시 h', async () => { const res = await load().fetch('/chat/resume', { method: 'POST', body: JSON.stringify({ user_id: 'u-3f9a2c1d', approved: true }) });
  assert.equal(res.status, 409); assert.deepEqual(res.body, hLines[1]); });
await check('/chat/pending shape {pending, proposal}', async () => { const api = load(); await stream(api, CASE3);
  const p = await api.fetch('/chat/pending?user_id=u-3f9a2c1d'); assert.deepEqual(Object.keys(p.body).sort(), ['pending', 'proposal']); assert.deepEqual(p.body.proposal, ex.b.proposal); });
await check('/health shape == 계약 §2-6', async () => { const h = await load().fetch('/health');
  const i = md.indexOf('### 2-6'); const s2 = md.indexOf('```json', i) + 7; assert.deepEqual(h.body, JSON.parse(md.slice(s2, md.indexOf('```', s2)))); });
await check('501자 → MESSAGE_TOO_LONG / 500자 통과', async () => { const f = await stream(load(), '먹'.repeat(501)); assert.equal(f[0].detail.code, 'MESSAGE_TOO_LONG');
  const ok = await stream(load(), '먹'.repeat(500)); assert.equal(ok.at(-2).type, 'FinalAnswer'); });
await check('D8 대기 중 새 질문 → cancelled_pending=true', async () => { const api = load(); await stream(api, CASE3); const r = sse(await stream(api, CASE1)); assert.equal(r.metadata.cancelled_pending, true); });
for (const r of results) console.log(r.join(' | '));
console.log(`PASS ${results.filter(r => r[0] === 'PASS').length} / FAIL ${results.filter(r => r[0] === 'FAIL').length}`);
