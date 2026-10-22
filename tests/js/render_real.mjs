// 실제 server.py 응답·SSE 프레임을 web_chat.html 의 화면 문구 함수에 넣어 본다.
// 사용: node render_real.mjs <프로젝트 루트> <frames.json>
// frames.json = [{name, frames:[...SSE 프레임], responses:[ChatResponse...]}]
// 출력: JSON {labels:[{case, type, label}], errors:[...]}
import fs from 'node:fs';

const [ROOT, FRAMES] = process.argv.slice(2);
const html = fs.readFileSync(`${ROOT}/web_chat.html`, 'utf8');
const cases = JSON.parse(fs.readFileSync(FRAMES, 'utf8'));

const cut = (from, to) => { const s = html.indexOf(from); const e = html.indexOf(to, s); if (s < 0 || e < 0) throw new Error(`cut ${from}`); return html.slice(s, e); };
const outer = cut('const won =', 'const APPROVAL_STATE_LABELS');
const inner = cut('const josa', 'function eventLabel');
const api = new Function(`${outer}\n${inner}\nreturn { STEP_LABELS, proposalText, proposalReason, relaxLines, excludedLines, reviewFlagFor, reviewTitle };`)();

const labels = [], errors = [];
const label = (c, e) => {
  const fn = api.STEP_LABELS[e.type];
  if (!fn) return null;
  try { return fn(e); } catch (err) { errors.push({ case: c, type: e.type, error: String(err) }); return null; }
};
for (const { name, frames, responses } of cases) {
  for (const f of frames || []) {
    if (typeof f !== 'object' || ['AnswerToken', 'FinalAnswer', 'Error'].includes(f.type)) continue;
    labels.push({ case: name, type: f.type, label: label(name, f) });
  }
  for (const r of responses || []) {
    for (const e of r.events) labels.push({ case: name + ':resp', type: e.type, label: label(name, e) });
    try {
      if (r.status === 'needs_approval') { labels.push({ case: name, type: 'approval', label: api.proposalText(r.proposal) + ' / ' + api.proposalReason(r.proposal) }); }
      if (r.status === 'done') {
        labels.push({ case: name, type: 'relaxLines', label: api.relaxLines(r.relaxed).join(' | ') });
        labels.push({ case: name, type: 'excludedLines', label: api.excludedLines(r.excluded).join(' | ') });
        for (const k of r.kept) { const f = api.reviewFlagFor(r, k); if (f) labels.push({ case: name, type: 'reviewTitle', label: api.reviewTitle(f) }); }
      }
    } catch (err) { errors.push({ case: name, type: 'render', error: String(err) }); }
  }
}
console.log(JSON.stringify({ labels, errors }));
