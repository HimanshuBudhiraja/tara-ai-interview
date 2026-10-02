/* Tara Agent Builder: the production client.
 *
 * The screens are the approved prototype's (design/tara-agent-builder-prototype.html),
 * unchanged in layout and wording. What changed is underneath: every model call
 * the prototype made in the browser is now a request to
 * /api/recruiter/agent-builder, and the voice test is a real Retell web call
 * on the one generic agent, configured per call by the server.
 *
 * Voice comes from Persona → Voice; length from Persona → Follow-up depth.
 * The server enforces both. The page only shows them.
 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const h = (tag, props, ...kids) => {
    const e = document.createElement(tag);
    if (props) for (const k in props) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') e.innerHTML = v; /* static icon markup only */
      else if (k === 'value') e.value = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat(3)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
    return e;
  };
  const SVG = (w, body, extra) => `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"${extra || ''}>${body}</svg>`;
  const SPARK = '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>';
  const ICON = {
    spark18: SVG(18, SPARK), spark16: SVG(16, SPARK), spark14: SVG(14, SPARK),
    check12: SVG(12, '<path d="M5 12.5l4.5 4.5L19 7.5"/>').replace('stroke-width="2"', 'stroke-width="3"'),
    check14: SVG(14, '<path d="M5 12.5l4.5 4.5L19 7.5"/>').replace('stroke-width="2"', 'stroke-width="3"'),
    check11: SVG(11, '<path d="M5 12.5l4.5 4.5L19 7.5"/>').replace('stroke-width="2"', 'stroke-width="3.4"'),
    send: SVG(18, '<path d="M5 12h14M13 6l6 6-6 6"/>'),
    edit: SVG(16, '<path d="M4 20h4L19 9l-4-4L4 16z"/>'),
    x: SVG(16, '<path d="M6 6l12 12M18 6L6 18"/>'),
    grip: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    warn: SVG(18, '<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17h.01"/>'),
    eyeoff: SVG(13, '<path d="M3 3l18 18"/><path d="M10.6 6.1A10 10 0 0 1 12 6c5 0 9 6 9 6a17 17 0 0 1-2.6 3.2M6.6 6.6C4.3 8.1 3 12 3 12s4 6 9 6a9 9 0 0 0 4.4-1.1"/>'),
    chev: SVG(20, '<path d="M6 9l6 6 6-6"/>'),
    clock: SVG(14, '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    mic: SVG(14, '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>')
  };
  document.querySelectorAll('[data-icon]').forEach((e) => { e.innerHTML = ICON[e.getAttribute('data-icon')]; });

  /* ---------------- API ---------------- */
  const API = '/api/recruiter/agent-builder';
  async function api(path, opts) {
    const o = opts || {};
    const r = await fetch(API + path, {
      method: o.method || 'GET', credentials: 'same-origin',
      headers: o.body ? { 'Content-Type': 'application/json' } : {},
      body: o.body ? JSON.stringify(o.body) : undefined
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty body */ }
    if (!r.ok) {
      const d = data && data.detail;
      const msg = typeof d === 'string' ? d
        : (d && d.message) || (r.status === 401 ? 'Sign in to the recruiter console, then reload this page.' : 'Something went wrong. Try again.');
      const err = new Error(msg); err.status = r.status; err.detail = d;
      if (r.status === 401) note(msg);
      throw err;
    }
    return data;
  }
  function note(msg) { const n = $('aiNote'); n.textContent = msg; n.hidden = !msg; }
  let toastT;
  function toast(msg) { const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 3800); }

  /* ---------------- state ---------------- */
  let OPT = { voices: [], depth_minutes: { Light: 10, Probing: 20, 'Deep dive': 30 }, model_configured: true, voice_configured: true };
  const S = {
    view: 1, brief: '', mode: 'roleplay', cat: 'all',
    building: false, stage: 0, buildErr: '', pf: null, pa: null, pc: null,
    row: null, agentId: '', fields: {}, agent: null, cfg: null, reviewed: false,
    askBusy: false, askMsg: '', askCls: '', genBusy: false, editQ: -1, advOpen: false,
    test: [], tBusy: false, tab: 'chat', score: null, scoreBusy: false,
    voice: { state: 'idle', callId: '', client: null, err: '' }
  };
  const initials = (n) => String(n || '').replace(/^(dr|mr|mrs|ms)\.?\s+/i, '').split(/\s+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'T';
  const voiceOf = (k) => OPT.voices.find((v) => v.key === k) || OPT.voices[0] || { key: k, label: k, language: 'English', locale: 'en-US' };
  function lengthParts() {
    const est = OPT.depth_minutes[S.cfg.depth] || 20;
    const cap = S.cfg.ending === 'Hard time limit' ? est : S.cfg.ending === 'No end time' ? 60 : est + 5;
    return { est, cap };
  }

  /* ---------------- navigation ---------------- */
  function go(v) {
    S.view = v;
    [1, 2, 3].forEach((i) => { $('v' + i).hidden = i !== v; });
    if (v === 3) renderS3();
    window.scrollTo(0, 0);
  }
  document.addEventListener('click', (e) => { const g = e.target.closest('[data-go]'); if (g) { e.preventDefault(); go(+g.getAttribute('data-go')); } });

  /* =============== SCREEN 1 =============== */
  const TEMPLATES = [
    { cat: 'ai', kind: 'Role-play', title: 'AI Engineer Technical Interview', desc: 'ML fundamentals, RAG vs fine-tuning, MLOps and responsible AI for mid-to-senior engineers.', mins: '25 min', format: 'Voice' },
    { cat: 'ai', kind: 'Role-play', title: 'Prompt Engineer Screening', desc: 'Prompt design, evaluation methods and failure analysis with live scenario questions.', mins: '20 min', format: 'Voice' },
    { cat: 'ai', kind: 'Role-play', title: 'ML Ops Engineer Deep-Dive', desc: 'Pipelines, drift monitoring, model serving and rollback strategy for production ML.', mins: '30 min', format: 'Voice' },
    { cat: 'ai', kind: 'Assessment', title: 'Data Scientist Case Study', desc: 'Candidate walks through a churn-prediction case: framing, features, metrics, trade-offs.', mins: '30 min', format: 'Voice' },
    { cat: 'sales', kind: 'Role-play', title: 'SDR Cold Call', desc: 'Tara plays a busy VP of Operations. Book a meeting in under five minutes.', mins: '10 min', format: 'Voice' },
    { cat: 'sales', kind: 'Role-play', title: 'Enterprise Discovery Call', desc: 'Uncover pain, budget and decision process with a cautious IT director.', mins: '20 min', format: 'Voice' },
    { cat: 'sales', kind: 'Role-play', title: 'Pricing Objection Handling', desc: 'A procurement lead pushes back hard on price. Defend value without discounting.', mins: '15 min', format: 'Voice' },
    { cat: 'sales', kind: 'Role-play', title: 'Renewal Negotiation', desc: 'An at-risk customer threatens to churn. Negotiate a renewal that protects margin.', mins: '20 min', format: 'Voice' },
    { cat: 'customer', kind: 'Role-play', title: 'Angry Customer De-escalation', desc: 'A frustrated customer after a failed delivery. Calm, own the issue, resolve.', mins: '10 min', format: 'Voice' },
    { cat: 'customer', kind: 'Role-play', title: 'Customer Success QBR', desc: 'Present quarterly outcomes to a skeptical sponsor and agree on next goals.', mins: '20 min', format: 'Voice' },
    { cat: 'customer', kind: 'Role-play', title: 'Support Agent Screening', desc: 'Empathy, troubleshooting and written clarity for frontline support hires.', mins: '15 min', format: 'Voice' },
    { cat: 'customer', kind: 'Role-play', title: 'Refund Request Handling', desc: 'Apply policy fairly when a loyal customer asks for an out-of-window refund.', mins: '10 min', format: 'Voice' }
  ];
  const CATS = { ai: 'AI Roles', sales: 'Sales', customer: 'Customer' };
  const SUGG = [
    { label: 'Senior AI Engineer technical interview', text: 'A 25-minute technical interview for a senior AI Engineer at DeepMind. Probe ML fundamentals, RAG vs fine-tuning, MLOps and responsible AI. Be friendly but rigorous.', mode: 'roleplay' },
    { label: 'SDR cold-call roleplay', text: 'A cold-call roleplay where Tara is a busy VP of Operations at a logistics company. The rep must book a 30-minute demo. Push back on timing twice.', mode: 'roleplay' },
    { label: 'Support de-escalation practice', text: 'An angry customer whose order arrived damaged for the second time. Score empathy, ownership and resolution. 10 minutes, voice.', mode: 'roleplay' }
  ];
  const MODES = [['roleplay', 'Role-play'], ['assessment', 'Assessment']];
  function renderS1() {
    const md = $('modes'); md.textContent = '';
    MODES.forEach(([id, label]) => md.append(h('button', { type: 'button', 'aria-pressed': String(S.mode === id), text: label, onclick: () => { S.mode = id; renderS1(); } })));
    const sg = $('sugg'); sg.textContent = '';
    SUGG.forEach((x) => sg.append(h('button', { type: 'button', text: x.label, onclick: () => { S.brief = x.text; $('brief').value = x.text; S.mode = x.mode; renderS1(); $('brief').focus(); } })));
    const tb = $('ttabs'); tb.textContent = '';
    [['all', 'All']].concat(Object.entries(CATS)).forEach(([id, label]) => {
      const n = id === 'all' ? TEMPLATES.length : TEMPLATES.filter((t) => t.cat === id).length;
      tb.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(S.cat === id), onclick: () => { S.cat = id; renderS1(); } }, label, h('i', { text: String(n) })));
    });
    const g = $('tcards'); g.textContent = '';
    TEMPLATES.filter((t) => S.cat === 'all' || t.cat === S.cat).forEach((t) => g.append(
      h('button', { class: 'tc', type: 'button', 'aria-label': 'Use template: ' + t.title, onclick: () => useTemplate(t) },
        h('span', { class: 'tr' }, h('span', { class: 'cat ' + t.cat, text: CATS[t.cat] }), h('span', { class: 'kind', text: t.kind })),
        h('span', { class: 'tt', text: t.title }),
        h('span', { class: 'td', text: t.desc }),
        h('span', { class: 'tf' }, h('span', null, h('span', null, h('span', { class: 'spark', html: ICON.clock }), t.mins), h('span', null, h('span', { class: 'spark', html: ICON.mic }), t.format)), h('span', { class: 'use', text: 'Use →' })))));
    syncCreate();
  }
  function syncCreate() { $('create').disabled = S.building || !S.brief.trim(); }
  function useTemplate(t) {
    S.brief = t.title + ': ' + t.desc + ' ' + t.mins + ', ' + t.format.toLowerCase() + '.';
    S.mode = t.kind === 'Assessment' ? 'assessment' : 'roleplay';
    $('brief').value = S.brief; renderS1(); build();
  }
  $('brief').addEventListener('input', (e) => { S.brief = e.target.value; syncCreate(); });
  $('brief').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !$('create').disabled) { e.preventDefault(); build(); } });
  $('create').addEventListener('click', build);
  $('navTpl').addEventListener('click', (e) => { e.preventDefault(); $('templates').scrollIntoView({ behavior: 'smooth' }); });
  $('navMine').addEventListener('click', (e) => { e.preventDefault(); $('mine').scrollIntoView({ behavior: 'smooth' }); });
  $('railAgent').addEventListener('click', (e) => { e.preventDefault(); go(1); loadMine(); });

  /* ---------- Skill Master: pick skills from the library, or add a custom one ---------- */
  function balanceWeights(rubric) {
    if (!rubric.length) return;
    const each = Math.floor(100 / rubric.length);
    rubric.forEach((r) => { r.weight = each; });
    rubric[0].weight += 100 - each * rubric.length;
  }
  let SM = null;
  async function openSkillMaster() {
    const dlg = $('smDlg');
    if (!SM) { try { SM = await api('/skill-master'); } catch (e) { toast(e.message); return; } }
    S.sm = { q: '', cat: 'all', picked: new Set() };
    renderSkillMaster(); dlg.showModal(); $('smSearch').value = ''; $('smSearch').focus();
  }
  function renderSkillMaster() {
    const st = S.sm, have = new Set(S.agent.rubric.map((r) => r.name.toLowerCase()));
    const tabs = $('smTabs'); tabs.textContent = '';
    [['all', 'All'], ['technical', 'Technical'], ['functional', 'Functional'], ['behavioural', 'Behavioural']].forEach(([id, label]) =>
      tabs.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(st.cat === id), text: label, onclick: () => { st.cat = id; renderSkillMaster(); } })));
    const q = st.q.trim().toLowerCase(), list = $('smList'); list.textContent = '';
    let shown = 0;
    SM.domains.filter((d) => st.cat === 'all' || d.category === st.cat).forEach((d) => {
      const skills = d.skills.filter((n) => !q || n.toLowerCase().includes(q) || d.label.toLowerCase().includes(q));
      if (!skills.length) return;
      shown += skills.length;
      list.append(h('div', { class: 'smdom' }, h('b', { text: d.label }), h('div', { class: 'smchips' }, skills.map((n) => {
        const already = have.has(n.toLowerCase()), on = st.picked.has(n);
        return h('button', { type: 'button', class: 'smchip', 'aria-pressed': String(on || already), disabled: already, title: already ? 'Already in this agent' : '',
          text: n, onclick: () => { on ? st.picked.delete(n) : st.picked.add(n); renderSkillMaster(); } });
      }))));
    });
    if (!shown) list.append(h('p', { class: 'smempty', text: q ? 'No skill in the library matches "' + st.q.trim() + '". Add it as a custom skill below.' : 'No skills here.' }));
    $('smCustomAdd').disabled = !$('smCustom').value.trim();
    $('smAdd').disabled = !st.picked.size;
    $('smAdd').textContent = st.picked.size ? 'Add ' + st.picked.size + ' skill' + (st.picked.size === 1 ? '' : 's') : 'Add skills';
  }
  $('smSearch').addEventListener('input', (e) => { S.sm.q = e.target.value; renderSkillMaster(); });
  $('smCustom').addEventListener('input', () => { $('smCustomAdd').disabled = !$('smCustom').value.trim(); });
  $('smCustomAdd').addEventListener('click', () => {
    const n = $('smCustom').value.trim().slice(0, 80); if (!n) return;
    S.sm.picked.add(n); $('smCustom').value = ''; renderSkillMaster();
  });
  $('smCancel').addEventListener('click', () => $('smDlg').close());
  $('smAdd').addEventListener('click', async () => {
    const names = [...S.sm.picked].filter((n) => !S.agent.rubric.some((r) => r.name.toLowerCase() === n.toLowerCase()));
    $('smDlg').close();
    if (!names.length) return;
    names.forEach((n) => S.agent.rubric.push({ name: n, anchor: '', weight: 0 }));
    balanceWeights(S.agent.rubric); S.reviewed = false; dirty = true; renderMain(); renderSide();
    toast('Added ' + names.length + ' skill' + (names.length === 1 ? '' : 's') + '. Tara is drafting what a 5 looks like and questions for ' + (names.length === 1 ? 'it' : 'them') + '…');
    try {
      await saveNow();
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/skills/draft', { method: 'POST', body: { names } });
      S.row = row; S.agent.rubric = row.agent.rubric; S.agent.questions = row.agent.questions;
      toast('Drafted anchors and ' + row.added + ' question' + (row.added === 1 ? '' : 's') + ' for the new skills. Review them before you publish.');
    } catch (e) { toast('Skills added. ' + e.message); }
    renderMain(); renderSide();
  });

  /* ---------- your role-plays: every agent created, to open or showcase ---------- */
  async function openAgent(id) {
    try {
      const row = await api('/agents/' + encodeURIComponent(id));
      S.brief = row.brief || ''; $('brief').value = S.brief; S.mode = row.mode === 'assessment' ? 'assessment' : 'roleplay';
      renderS1(); loadRow(row); go(3);
      history.replaceState(null, '', '?agent=' + encodeURIComponent(id));
    } catch (e) { toast('That agent couldn\'t be opened.'); }
  }
  async function loadMine() {
    let list = [];
    try { list = (await api('/agents')).agents || []; } catch (e) { return; }
    $('mine').hidden = !list.length; $('navMine').hidden = !list.length;
    const g = $('minecards'); g.textContent = '';
    list.forEach((a) => {
      const pub = a.published_version > 0;
      g.append(h('button', { class: 'tc', type: 'button', 'aria-label': 'Open ' + a.title, onclick: () => openAgent(a.agent_id) },
        h('span', { class: 'tr' }, h('span', { class: 'cat ' + (pub ? 'pub' : 'draft'), text: pub ? 'Published · v' + a.published_version : 'Draft' }), h('span', { class: 'kind', text: a.type_label })),
        h('span', { class: 'tt', text: a.title }),
        h('span', { class: 'td', text: a.description }),
        h('span', { class: 'tf' }, h('span', null,
          h('span', null, h('span', { class: 'spark', html: ICON.clock }), (a.minutes || '–') + ' min'),
          h('span', null, h('span', { class: 'spark', html: ICON.mic }), a.persona || 'Voice')), h('span', { class: 'use', text: 'Open →' }))));
    });
  }

  /* =============== SCREEN 2 =============== */
  const STEPS = [
    { label: 'Reading your brief', detail: 'Type, role, skills and tone' },
    { label: 'Setting scenario details', detail: '' },
    { label: 'Designing the Tara persona', detail: 'Name, background, voice and style' },
    { label: 'Drafting potential AI questions', detail: 'Core questions plus follow-up paths' },
    { label: 'Choosing the skills to score', detail: 'Skills, weights, 1–5 anchors' },
    { label: 'Configuring the conversation', detail: 'Opening, pacing and ending' }
  ];
  // Two real stages from the server: the plan (steps 1–3), then the content (4–5), then the saved agent (6).
  const doneSteps = () => (S.stage >= 3 ? 6 : S.stage === 2 ? 5 : S.stage === 1 ? 3 : 0);
  function renderS2() {
    const at = doneSteps(), done = S.stage >= 3;
    $('genh').textContent = done ? 'Your agent draft is ready' : (S.buildErr ? 'Tara stopped building' : 'Tara is building your agent…');
    $('briefTxt').textContent = S.brief;
    const pct = Math.round(at / 6 * 100);
    $('progLbl').textContent = done ? 'All set' : 'Step ' + Math.min(at + 1, 6) + ' of 6';
    $('progPct').textContent = pct + '%';
    $('progBar').setAttribute('aria-valuenow', pct);
    $('progBar').firstElementChild.style.width = pct + '%';
    const F = (S.pf || {});
    const ol = $('steps2'); ol.textContent = '';
    STEPS.forEach((s, i) => {
      const st = i < at ? 'done' : (i === at && !S.buildErr ? 'act' : 'wait');
      const detail = i === 1 ? ([F.type, F.role, F.difficulty].filter(Boolean).join(' · ') || 'Type, role and difficulty') : s.detail;
      ol.append(h('li', { class: st }, h('span', { class: 'sdot ' + st, html: st === 'done' ? ICON.check14 : '' }), h('span', null, h('b', { text: s.label }), h('small', { text: detail }))));
    });
    $('err2').hidden = !S.buildErr; $('err2').textContent = S.buildErr;
    $('act2').hidden = !done;
    $('rebuild').disabled = S.building;
    const pa = S.pa, pc = S.pc;
    const card = $('draftCard'); card.textContent = '';
    const bone = (st) => h('span', { class: 'bone', style: st });
    card.append(
      h('div', { class: 'dh' },
        pa ? h('span', { class: 'bigava fadein', text: initials(pa.persona.name) }) : h('span', { class: 'bigava ph' }),
        h('div', { class: 't' },
          pa ? h('span', { class: 'dtitle fadein', text: pa.title }) : bone('height:18px;width:70%'),
          pa ? h('span', { class: 'dsub fadein', text: [pa.persona.name, pa.persona.role, pa.persona.style, 'Voice: ' + voiceOf(pa.voice).label.split(' —')[0]].filter(Boolean).join(' · ') }) : bone('height:12px;width:50%;background:#F4F4F8'))),
      h('div', { class: 'facts' }, [
        ['Scenario type', pa && (pa.type_label || F.type)],
        ['Difficulty', F.difficulty],
        ['Format', pa && ('Voice · ' + (OPT.depth_minutes[pa.depth] || 20) + ' min')],
        ['Opens with', pa && 'Tara greets']
      ].map(([k, v]) => h('div', { class: 'fact' }, h('span', { text: k }), v ? h('b', { class: 'fadein', text: v }) : bone('height:14px;width:60%;margin-top:3px;background:#EFEFF5')))),
      h('div', { style: 'display:flex;flex-direction:column;gap:10px' }, h('span', { class: 'eyebrow', text: 'POTENTIAL AI QUESTIONS' }),
        pc && pc.questions.length ? h('div', { class: 'fadein', style: 'display:flex;flex-direction:column;gap:8px' }, pc.questions.slice(0, 2).map((q) => h('div', { class: 'dq', text: q.text })), pc.questions.length > 2 ? h('div', { class: 'more', text: '+ ' + (pc.questions.length - 2) + ' more question' + (pc.questions.length - 2 === 1 ? '' : 's') }) : null)
          : h('div', { style: 'display:flex;flex-direction:column;gap:8px' }, bone('height:42px;border-radius:12px;background:#F4F4F8'), bone('height:42px;border-radius:12px;background:#F6F6F9'))),
      h('div', { style: 'display:flex;flex-direction:column;gap:10px' }, h('span', { class: 'eyebrow', text: 'SKILLS' }),
        pc && pc.rubric.length ? h('div', { class: 'rchips fadein' }, pc.rubric.map((r) => h('span', { class: 'rchip', text: r.name + ' · ' + r.weight + '%' })))
          : h('div', { style: 'display:flex;gap:8px' }, [140, 110, 90].map((w) => bone('height:30px;width:' + w + 'px;border-radius:999px;background:#F4F4F8'))))
    );
  }
  async function build() {
    if (S.building || !S.brief.trim()) return;
    S.building = true; S.stage = 0; S.buildErr = ''; S.pf = S.pa = S.pc = null;
    go(2); renderS2(); syncCreate();
    try {
      const r = await fetch(API + '/drafts', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brief: S.brief.trim(), mode: S.mode })
      });
      if (!r.ok) {
        let d = null; try { d = await r.json(); } catch (e) { /* empty */ }
        throw new Error(r.status === 401 ? 'Sign in to the recruiter console, then reload this page.'
          : r.status === 429 ? 'Too many drafts in a short time. Wait a few minutes, then try again.'
            : (d && typeof d.detail === 'string' && d.detail) || 'Tara couldn\'t start the draft. Try again.');
      }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = '', finished = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = (chunk.match(/^event: (.*)$/m) || [])[1];
          const raw = (chunk.match(/^data: (.*)$/m) || [])[1];
          if (!ev || !raw) continue;
          const data = JSON.parse(raw);
          if (ev === 'plan') { S.stage = 1; S.pf = data.fields; S.pa = data.agent; if (data.drafted_by === 'template') note('No model is configured, so Tara filled this draft from a template. Edit it on the next screen.'); }
          else if (ev === 'content') { S.stage = 2; S.pc = data; }
          else if (ev === 'done') { S.stage = 3; loadRow(data); finished = true; }
          else if (ev === 'error') throw new Error(data.message || 'Tara couldn\'t finish the draft. Try again.');
          renderS2();
        }
      }
      if (!finished) throw new Error('The connection closed before the draft finished. Try again.');
    } catch (e) {
      S.buildErr = e.message || 'Tara couldn\'t finish the draft. Try again.';
    }
    S.building = false; renderS2(); syncCreate();
  }
  $('rebuild').addEventListener('click', () => build());
  $('toReview').addEventListener('click', () => go(3));

  /* =============== SCREEN 3 =============== */
  function loadRow(row) {
    S.row = row; S.agentId = row.agent_id;
    S.fields = row.fields; S.agent = row.agent; S.cfg = row.cfg; S.reviewed = row.reviewed;
    S.askMsg = ''; S.editQ = -1;
    try { history.replaceState(null, '', '?agent=' + encodeURIComponent(row.agent_id)); } catch (e) { /* ignore */ }
    resetTest(false);
  }
  function seg(label, key, opts) {
    return h('div', { role: 'group', 'aria-label': label, class: 'seg' }, opts.map((o) => h('button', { type: 'button', 'aria-pressed': String(S.cfg[key] === o), text: o, onclick: () => { S.cfg[key] = o; touch(); renderMain(); } })));
  }
  function readiness() {
    const a = S.agent, f = S.fields;
    const tot = a.rubric.reduce((t, r) => t + (+r.weight || 0), 0);
    const rubOk = S.reviewed && tot === 100 && a.rubric.length > 0;
    return [
      { id: 'scenario', label: 'Scenario details', group: 'BASICS', done: !!(f.type && f.role && f.skills) },
      { id: 'desc', label: 'Description', group: 'BASICS', done: !!a.description.trim() },
      { id: 'context', label: 'Instructions', group: 'AGENT SETUP', done: !!a.instructions.trim() },
      { id: 'persona', label: 'Persona', group: 'AGENT SETUP', done: !!a.persona.name.trim() },
      { id: 'rubric', label: 'Skills', group: 'AGENT SETUP', done: rubOk, review: !rubOk },
      { id: 'questions', label: 'Potential AI Questions', group: 'OPTIONAL', opt: true, done: a.questions.length > 0 },
      { id: 'settings', label: 'Conversation settings', group: 'OPTIONAL', opt: true },
      { id: 'advanced', label: 'Advanced', group: 'OPTIONAL', opt: true }
    ];
  }
  function renderSide() {
    const a = S.agent, side = $('side'); side.textContent = '';
    const items = readiness(), req = items.filter((i) => !i.opt), n = req.filter((i) => i.done).length;
    side.append(
      h('div', { class: 'sid' }, h('span', { class: 'bigava', text: initials(a.persona.name) }), h('div', null, h('b', { text: a.title }), h('small', { text: a.type_label }))),
      h('div', { class: 'ready' }, h('div', { class: 'top' }, h('span', { text: 'Ready to publish' }), h('b', { text: n + ' of ' + req.length })), h('div', { class: 'pbar' }, h('i', { style: 'width:' + Math.round(n / req.length * 100) + '%' })))
    );
    const cl = S.row && S.row.candidate_link;
    if (cl) {
      const url = location.origin + cl.path;
      side.append(h('div', { class: 'clink' },
        h('div', { class: 'top' }, h('span', { text: 'Candidate link' }), h('small', { text: 'v' + S.row.published_version })),
        h('a', { href: url, target: '_blank', rel: 'noopener', text: url.replace(/^https?:\/\//, '') }),
        h('div', { class: 'row' }, h('span', { class: 'code', text: cl.code }),
          h('button', { class: 'obtn ghost', type: 'button', text: 'Copy link', onclick: (e) => { const b = e.currentTarget; navigator.clipboard.writeText(url).then(() => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy link'; }, 1500); }, () => toast('Copy was blocked. Select the link instead.')); } }))));
    }
    ['BASICS', 'AGENT SETUP', 'OPTIONAL'].forEach((g) => {
      side.append(h('div', { class: 'ngroup' }, h('span', { text: g }), items.filter((i) => i.group === g).map((i) =>
        h('a', { class: 'navi', href: '#' + i.id, onclick: (e) => { e.preventDefault(); const t = $(i.id); if (t) { t.scrollIntoView({ behavior: 'smooth', block: 'start' }); t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash'); } } },
          i.label, i.review ? h('span', { class: 'revpill', text: 'Review' }) : i.done ? h('span', { class: 'okdot', 'aria-label': 'Complete', html: ICON.check11 }) : null))));
    });
    $('crumbT').textContent = a.title;
    const pub = S.row && S.row.published_version;
    $('dpill').textContent = pub ? 'PUBLISHED · v' + pub : 'DRAFT';
    $('dpill').className = 'draftpill' + (pub ? ' pub' : '');
    $('invBtn').hidden = !pub;
  }

  /* ---- autosave ---- */
  let saveT = null, saving = null, dirty = false;
  function setSaved(t) { $('saved').lastElementChild.textContent = t; }
  function touch() {
    dirty = true; setSaved('Saving…');
    if (S.cfg) S.fields.length = lengthParts().est + ' min';
    renderSide();
    clearTimeout(saveT); saveT = setTimeout(() => { saveNow(); }, 700);
  }
  async function saveNow() {
    clearTimeout(saveT);
    if (saving) { await saving; }
    if (!dirty || !S.agentId) return;
    dirty = false;
    saving = api('/agents/' + encodeURIComponent(S.agentId), { method: 'PUT', body: { fields: S.fields, agent: S.agent, cfg: S.cfg, reviewed: S.reviewed } })
      .then((row) => {
        S.row = row;
        // Keep the objects the inputs are bound to; take only what the server derives.
        S.cfg.language = row.cfg.language; S.fields.length = row.fields.length;
        setSaved('Saved just now'); renderSide();
      })
      .catch((e) => { dirty = true; setSaved('Not saved'); toast(e.message || 'Couldn\'t save. Check your connection.'); })
      .finally(() => { saving = null; });
    return saving;
  }
  window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  const bind = (obj, key, after) => (e) => { obj[key] = e.target.value; touch(); if (after) after(); };


  function renderMain() {
    const a = S.agent, c = S.cfg, m = $('main3');
    const keepY = window.scrollY;
    m.textContent = '';
    const tot = a.rubric.reduce((t, r) => t + (+r.weight || 0), 0);
    const quick = ['Make it more challenging', 'Add a coding question', 'Make it shorter', 'Make the persona warmer'];
    const noModel = !OPT.model_configured;

    const askIn = h('input', { id: 'ask', type: 'text', autocomplete: 'off', placeholder: 'Tell Tara what to change — “Make it harder and add a coding question”', disabled: S.askBusy || noModel || null });
    const askBtn = h('button', { class: 'sq', type: 'button', 'aria-label': 'Send to Tara', html: ICON.send, disabled: true });
    const syncAsk = () => { askBtn.disabled = S.askBusy || noModel || !askIn.value.trim(); };
    askIn.addEventListener('input', syncAsk);
    askIn.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !askBtn.disabled) { e.preventDefault(); revise(askIn.value); } });
    askBtn.addEventListener('click', () => revise(askIn.value));
    m.append(h('section', { class: 'askbox', 'aria-label': 'Ask Tara to edit' },
      h('div', { class: 'askrow' }, h('span', { class: 'askic', html: ICON.spark18 }), h('label', { class: 'sr', for: 'ask', text: 'Ask Tara to change something' }), askIn, askBtn),
      h('div', { class: 'askchips' }, quick.map((q) => h('button', { type: 'button', class: 'ghost', text: q, disabled: S.askBusy || noModel || null, onclick: () => revise(q) }))),
      (S.askBusy || S.askMsg || noModel) ? h('p', { class: 'askstat ' + (noModel ? 'bad' : S.askCls || ''), role: 'status', text: noModel ? 'Ask Tara needs a model. Set OPENROUTER_API_KEY on the server.' : S.askBusy ? 'Tara is updating the agent…' : S.askMsg }) : null
    ));
    syncAsk();

    const sel = (id, key, opts, obj) => h('select', { id, class: 'inp', onchange: bind(obj || S.fields, key, () => { renderSide(); }) }, opts.map((o) => h('option', { value: o, text: o, selected: (obj || S.fields)[key] === o || null })));
    const typeOpts = ['Role-play', 'Assessment'];
    m.append(h('section', { id: 'scenario', class: 'card', 'aria-labelledby': 'sc-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'sc-h', text: 'Scenario details' }), h('p', { class: 'sub', text: 'Tara filled these from your brief. Change any of them here.' }))),
      h('div', { class: 'grid4' },
        h('div', { class: 'fld' }, h('label', { for: 'f-type', text: 'Scenario type' }), sel('f-type', 'type', typeOpts)),
        h('div', { class: 'fld' }, h('label', { for: 'f-role', text: 'Role or situation' }), h('input', { id: 'f-role', class: 'inp', type: 'text', value: S.fields.role || '', oninput: bind(S.fields, 'role') })),
        h('div', { class: 'fld', style: 'grid-column:1 / -1' }, h('label', { for: 'f-skills', text: 'What to assess' }), h('input', { id: 'f-skills', class: 'inp', type: 'text', value: S.fields.skills || '', oninput: bind(S.fields, 'skills') }))
      )));

    m.append(h('section', { id: 'desc', class: 'card', style: 'gap:14px', 'aria-labelledby': 'desc-h' },
      h('div', { class: 'tt', style: 'display:flex;flex-direction:column;gap:6px' }, h('h2', { id: 'desc-h', text: 'Participant-facing description' }), h('p', { class: 'sub', text: 'Shown to participants before they start. Keep it short and encouraging.' })),
      h('label', { class: 'sr', for: 'desc-t', text: 'Description' }),
      h('textarea', { id: 'desc-t', class: 'inp', rows: '3', value: a.description, oninput: bind(a, 'description') })));

    m.append(h('section', { id: 'context', class: 'card', style: 'gap:14px', 'aria-labelledby': 'ctx-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'ctx-h', text: "Tara's instructions" }), h('p', { class: 'sub', text: 'How Tara runs the conversation. Hidden from participants.' })), h('span', { class: 'privpill' }, h('span', { class: 'spark', html: ICON.eyeoff }), 'Private')),
      h('label', { class: 'sr', for: 'ctx-t', text: "Tara's instructions" }),
      h('textarea', { id: 'ctx-t', class: 'inp', rows: '6', value: a.instructions, oninput: bind(a, 'instructions') }),
      h('div', { class: 'grid4' },
        h('div', { class: 'fld' }, h('label', { for: 'open-t', text: 'Opening line' }), h('textarea', { id: 'open-t', class: 'inp', rows: '3', value: a.opening_line, oninput: bind(a, 'opening_line') })),
        h('div', { class: 'fld' }, h('label', { for: 'close-t', text: 'Closing line' }), h('textarea', { id: 'close-t', class: 'inp', rows: '3', value: a.closing_line, oninput: bind(a, 'closing_line') })))));

    m.append(h('section', { id: 'persona', class: 'card', style: 'gap:22px', 'aria-labelledby': 'per-h' },
      h('div', { class: 'tt', style: 'display:flex;flex-direction:column;gap:6px' }, h('h2', { id: 'per-h', text: 'Persona' }), h('p', { class: 'sub', text: 'Who participants will meet. Change the name and it updates everywhere the participant hears or reads it.' })),
      h('div', { class: 'prow' },
        h('div', { class: 'pava' }, h('span', { class: 'bigava', id: 'pAva', text: initials(a.persona.name) })),
        h('div', { class: 'pgrid' },
          h('div', { class: 'fld' }, h('label', { for: 'pn', text: 'Name' }), h('input', { id: 'pn', class: 'inp', type: 'text', value: a.persona.name, onfocus: () => { S.nameWas = a.persona.name; }, oninput: (e) => { a.persona.name = e.target.value; $('pAva').textContent = initials(a.persona.name); syncTestHead(); touch(); }, onchange: (e) => { const n = renameEverywhere(S.nameWas, e.target.value); S.nameWas = e.target.value; if (n) { touch(); renderMain(); toast('Updated the name in ' + n + ' place' + (n === 1 ? '' : 's') + '.'); } } })),
          h('div', { class: 'fld' }, h('label', { for: 'pt', text: 'Title' }), h('input', { id: 'pt', class: 'inp', type: 'text', value: a.persona.role, oninput: (e) => { a.persona.role = e.target.value; syncTestHead(); touch(); } })),
          h('div', { class: 'fld' }, h('label', { for: 'pv', text: 'Voice' }),
            h('select', { id: 'pv', class: 'inp', onchange: (e) => { setVoice(e.target.value); } },
              OPT.voices.map((v) => h('option', { value: v.key, text: v.label, selected: c.voice === v.key || null })))))),
      h('div', { class: 'segs2' },
        h('div', { class: 'segwrap' }, h('span', { class: 'lbl', text: 'Difficulty' }), seg('Difficulty', 'tone', ['Friendly', 'Realistic', 'Tough'])),
        h('div', { class: 'segwrap' }, h('span', { class: 'lbl', text: 'Follow-up depth' }), seg('Follow-up depth', 'depth', ['Light', 'Probing', 'Deep dive']),
          h('small', { style: 'font-size:13px;color:var(--muted-2)', text: 'Also sets the length: Light ≈ ' + OPT.depth_minutes.Light + ' min · Probing ≈ ' + OPT.depth_minutes.Probing + ' min · Deep dive ≈ ' + OPT.depth_minutes['Deep dive'] + ' min' })))));

    const rows = a.rubric.map((r, i) => {
      const w = h('input', { type: 'number', min: '0', max: '100', value: String(r.weight), 'aria-label': 'Weight for ' + r.name, oninput: (e) => { r.weight = Math.max(0, Math.round(+e.target.value || 0)); S.reviewed = false; touch(); updTotal(); } });
      return h('div', { class: 'rr' },
        h('textarea', { class: 'ed n', rows: '1', 'aria-label': 'Skill name', value: r.name, oninput: (e) => { r.name = e.target.value; S.reviewed = false; touch(); } }),
        h('div', { class: 'a-cell' }, h('textarea', { class: 'ed a', rows: '2', 'aria-label': 'What a 5 looks like', value: r.anchor, oninput: (e) => { r.anchor = e.target.value; S.reviewed = false; touch(); } })),
        h('div', { class: 'wbox' }, w, h('span', { text: '%' })),
        h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Remove ' + r.name, html: ICON.x, onclick: () => { a.rubric.splice(i, 1); S.reviewed = false; touch(); renderMain(); } }));
    });
    const totPill = h('span', { class: 'totpill' + (tot === 100 ? '' : ' off'), id: 'totp', text: 'Total ' + tot + '%' });
    function updTotal() { const t = a.rubric.reduce((x, r) => x + (+r.weight || 0), 0); totPill.textContent = 'Total ' + t + '%'; totPill.className = 'totpill' + (t === 100 ? '' : ' off'); renderSide(); }
    m.append(h('section', { id: 'rubric', class: 'card', 'aria-labelledby': 'rub-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'rub-h', text: 'Skills' }), h('p', { class: 'sub', text: 'Each skill is scored 1–5 with anchored descriptors. Weights must total 100%. Never sent to the voice agent.' })), totPill),
      !S.reviewed ? h('div', { class: 'warnbox' }, h('span', null, h('span', { class: 'spark', html: ICON.warn }), 'Tara drafted these skills from your brief. Check the weights before you publish.'),
        h('button', { class: 'fillbtn', type: 'button', text: 'Mark as reviewed', onclick: () => { const t = a.rubric.reduce((x, r) => x + (+r.weight || 0), 0); if (t !== 100) { toast('Weights total ' + t + '%. Make them add up to 100% first.'); return; } S.reviewed = true; touch(); renderMain(); } })) : null,
      h('div', { class: 'rtab' }, h('div', { class: 'rr hd' }, h('span', { text: 'SKILL' }), h('span', { class: 'a-cell', text: 'WHAT A 5 LOOKS LIKE' }), h('span', { style: 'text-align:right', text: 'WEIGHT' }), h('span')), rows),
      h('div', { class: 'rbtns' },
        h('button', { class: 'dashbtn ghost', type: 'button', text: '+ Add skill from Skill Master', onclick: openSkillMaster }),
        h('button', { class: 'obtn ghost', type: 'button', text: 'Balance weights to 100%', disabled: !a.rubric.length, onclick: () => { balanceWeights(a.rubric); S.reviewed = false; touch(); renderMain(); } }))));

    const qlist = h('div', { class: 'qlist' });
    let dragFrom = -1;
    a.questions.forEach((q, i) => {
      const editing = S.editQ === i;
      const body = editing
        ? h('div', { class: 'qbody' }, h('textarea', { rows: '2', 'aria-label': 'Question text', value: q.text, onblur: (e) => { q.text = e.target.value.trim() || q.text; S.editQ = -1; touch(); renderMain(); }, onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.target.blur(); } } }), q.tag ? h('span', { class: 'qtag', text: q.tag }) : null)
        : h('div', { class: 'qbody' }, h('span', { class: 'qt', text: q.text }), q.tag ? h('span', { class: 'qtag', text: q.tag }) : null);
      const row = h('div', { class: 'qrow', draggable: 'true' },
        h('span', { class: 'grip', 'aria-hidden': 'true', html: ICON.grip }), body,
        h('button', { class: 'xbtn', style: 'color:var(--accent-ink)', type: 'button', 'aria-label': 'Edit question', html: ICON.edit, onclick: () => { S.editQ = i; renderMain(); const t = $('main3').querySelector('.qbody textarea'); if (t) t.focus(); } }),
        h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Remove question', html: ICON.x, onclick: () => { a.questions.splice(i, 1); touch(); renderMain(); } }));
      row.addEventListener('dragstart', () => { dragFrom = i; row.classList.add('dragging'); });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', (e) => { e.preventDefault(); row.classList.add('over'); });
      row.addEventListener('dragleave', () => row.classList.remove('over'));
      row.addEventListener('drop', (e) => { e.preventDefault(); if (dragFrom < 0 || dragFrom === i) return; const [mv] = a.questions.splice(dragFrom, 1); a.questions.splice(i, 0, mv); dragFrom = -1; touch(); renderMain(); });
      qlist.append(row);
    });
    m.append(h('section', { id: 'questions', class: 'card', 'aria-labelledby': 'q-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'q-h' }, 'Potential AI Questions ', h('small', { text: '(Optional)' })), h('p', { class: 'sub', text: 'Tara picks from these based on how the conversation goes. Drag to set priority.' })),
        h('button', { class: 'obtn ghost', type: 'button', disabled: S.genBusy || noModel || null, onclick: genMore }, h('span', { class: 'spark', html: ICON.spark16 }), S.genBusy ? 'Generating…' : 'Generate more')),
      qlist,
      h('div', { class: 'togrow' }, h('span', null, h('b', { text: 'Adaptive follow-ups' }), h('small', { text: 'Tara asks probing follow-ups when an answer is vague or strong.' })),
        h('button', { class: 'sw', type: 'button', role: 'switch', 'aria-checked': String(c.followups), 'aria-label': 'Adaptive follow-ups', onclick: () => { c.followups = !c.followups; touch(); renderMain(); } }, h('i'))),
      h('button', { class: 'dashbtn ghost', type: 'button', text: '+ Add question', onclick: () => { a.questions.push({ text: 'New question', tag: '' }); S.editQ = a.questions.length - 1; touch(); renderMain(); const t = $('main3').querySelector('.qbody textarea'); if (t) { t.focus(); t.select(); } } })));

    const srow = (title, hint, key, opts) => h('div', { class: 'srow' }, h('span', { class: 'tt' }, h('b', { text: title }), h('small', { text: hint })), seg(title, key, opts));
    const L = lengthParts();
    m.append(h('section', { id: 'settings', class: 'card', style: 'gap:22px', 'aria-labelledby': 'set-h' },
      h('div', { class: 'tt', style: 'display:flex;flex-direction:column;gap:6px' }, h('h2', { id: 'set-h', text: 'Conversation settings' }), h('p', { class: 'sub', text: 'How the session starts, runs and ends.' })),
      srow('Who speaks first', 'Tara greets the participant in character.', 'speaker', ['Tara opens', 'Participant opens']),
      srow('Introduction sound', 'Played as the session begins.', 'sound', ['None', 'Phone ring', 'Video join', 'Doorbell']),
      srow('Ending', 'How the conversation wraps up.', 'ending', ['Tara decides', 'Hard time limit', 'No end time']),
      h('div', { class: 'srow', style: 'border-bottom:none;padding-bottom:0' },
        h('span', { class: 'tt' }, h('b', { text: 'Conversation length' }), h('small', { text: 'Set by Follow-up depth in Persona (' + c.depth + '). Change it there.' })),
        h('span', { style: 'display:flex;flex-direction:column;gap:4px' },
          h('span', { class: 'est' }, h('b', { text: '≈ ' + L.est }), h('span', { text: 'min' })),
          h('span', { class: 'estcalc', text: 'Call cap ' + L.cap + ' min (' + c.ending + ')' })))));

    m.append(h('section', { id: 'advanced', class: 'adv' },
      h('button', { type: 'button', 'aria-expanded': String(S.advOpen), onclick: () => { S.advOpen = !S.advOpen; renderMain(); } },
        h('span', null, h('b', { text: 'Advanced' }), h('small', { text: 'Language, recording consent' })), h('span', { class: 'spark', style: S.advOpen ? 'transform:rotate(180deg)' : null, html: ICON.chev })),
      S.advOpen ? h('div', { class: 'advbody' },
        h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Language' }), h('p', { style: 'font-size:15px;margin:6px 0 0', text: voiceOf(c.voice).language + ' — set by the voice chosen in Persona' })),
        h('div', { class: 'togrow', style: 'grid-column:1 / -1' }, h('span', null, h('b', { text: 'Recording consent' }), h('small', { text: 'Ask participants to agree to recording before they start.' })),
          h('button', { class: 'sw', type: 'button', role: 'switch', 'aria-checked': String(c.consent), 'aria-label': 'Recording consent', onclick: () => { c.consent = !c.consent; touch(); renderMain(); } }, h('i')))) : null));


    window.scrollTo(0, keepY);
  }
  /* The persona's name follows the voice: pick Adrian and the persona is
   * Adrian, in the name field, the opening line and the instructions. */
  const voiceName = (k) => voiceOf(k).label.split(' —')[0];
  /* Swap the persona's name everywhere the participant hears or reads it:
   * title, opening, closing, instructions, description and every question.
   * Matches the full name, the name without a title ("Dr."), and the first name. */
  function renameEverywhere(before, after) {
    const a = S.agent;
    before = (before || '').trim(); after = (after || '').trim();
    if (!before || !after || before === after) return 0;
    const bare = before.replace(/^(dr|mr|mrs|ms|prof)\.?\s+/i, '');
    const afterFirst = after.replace(/^(dr|mr|mrs|ms|prof)\.?\s+/i, '').split(/\s+/)[0];
    // Longest first, so "Dr. Maya Rao" is replaced whole before "Maya" alone.
    const pairs = [[before, after], [bare, after], [bare.split(/\s+/)[0], afterFirst]]
      .filter(([n], i, all) => n && all.findIndex(([m]) => m === n) === i).sort((x, y) => y[0].length - x[0].length);
    const swap = (t) => { pairs.forEach(([n, to]) => { t = t.replace(new RegExp('\\b' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'g'), to); }); return t; };
    let changed = 0;
    ['title', 'opening_line', 'closing_line', 'instructions', 'description'].forEach((k) => { const t = swap(a[k] || ''); if (t !== a[k]) { a[k] = t; changed++; } });
    (a.questions || []).forEach((q) => { const t = swap(q.text || ''); if (t !== q.text) { q.text = t; changed++; } });
    return changed;
  }
  function setVoice(key) {
    const a = S.agent, c = S.cfg, before = a.persona.name, after = voiceName(key);
    c.voice = key; c.language = voiceOf(key).language;
    renameEverywhere(before, after);
    a.persona.name = after;
    touch(); renderMain(); syncTestHead(); resetTest(true);
  }
  function syncTestHead() {
    const a = S.agent; if (!a) return;
    $('tAva').textContent = initials(a.persona.name); $('tName').textContent = a.persona.name; $('tRole').textContent = a.persona.role || a.type_label;
  }
  function renderS3() { if (!S.agent) { go(1); return; } renderSide(); renderMain(); syncTestHead(); renderTest(); }

  async function revise(instr) {
    instr = (instr || '').trim(); if (!instr || S.askBusy) return;
    S.askBusy = true; S.askMsg = ''; renderMain();
    try {
      await saveNow();
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/revise', { method: 'POST', body: { instruction: instr } });
      loadRow(row); S.askMsg = (row.summary || 'Updated.') + ' The test conversation restarted with this version.'; S.askCls = 'ok';
    } catch (e) { S.askMsg = e.message; S.askCls = 'bad'; }
    S.askBusy = false; renderS3();
  }
  async function genMore() {
    if (S.genBusy) return;
    S.genBusy = true; renderMain();
    try {
      await saveNow();
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/questions/generate', { method: 'POST' });
      S.row = row; S.agent.questions = row.agent.questions;
      toast('Added ' + row.added + ' question' + (row.added === 1 ? '' : 's') + '.');
    } catch (e) { toast(e.message); }
    S.genBusy = false; renderMain(); renderSide();
  }
  $('pubBtn').addEventListener('click', async () => {
    const missing = readiness().filter((i) => !i.opt && !i.done);
    if (missing.length) { toast('Finish ' + missing.map((i) => i.label.toLowerCase()).join(', ') + ' before publishing.'); const t = $(missing[0].id); if (t) t.scrollIntoView({ behavior: 'smooth' }); return; }
    try {
      await saveNow();
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/publish', { method: 'POST' });
      S.row = row; renderSide(); toast('Published as version ' + row.published_version + '. The candidate link is in the sidebar.');
    } catch (e) {
      const d = e.detail || {};
      toast(d.missing ? 'Finish ' + d.missing.join(', ').toLowerCase() + ' first.' : d.leaked ? 'The instructions or questions repeat a skill description. Reword them first.' : e.message);
    }
  });
  // One access code per participant, for the published version. The link opens the participant flow.
  $('invBtn').addEventListener('click', async () => {
    try {
      const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/invites', { method: 'POST', body: {} });
      let copied = false;
      try { await navigator.clipboard.writeText(r.link); copied = true; } catch (e) { /* not allowed here */ }
      toast((copied ? 'Link copied. ' : '') + 'Access code ' + r.code + ' · ' + r.link);
    } catch (e) { toast(e.message); }
  });
  $('testBtn').addEventListener('click', () => { resetTest(true); $('testp').scrollIntoView({ behavior: 'smooth', block: 'start' }); if (OPT.voice_configured) startVoice(); });

  /* ---------- test: a real voice call, shown as its transcript ----------
   * The test is spoken: the button places a real Retell call on the one agent,
   * and the log only shows that call's timed transcript. Scoring reads the
   * transcript once the call has ended. */
  function resetTest(render) {
    endVoice();
    S.tBusy = false; S.score = null; S.scoreBusy = false;
    S.voice = { state: 'idle', callId: '', client: null, err: '' };
    S.test = [];
    if (render !== false) renderTest();
  }
  const turns = () => S.test.filter((m) => m.from !== 'err').map((m) => ({ role: m.from === 'tara' ? 'agent' : 'user', text: m.text }));
  const inCall = () => ['connecting', 'live', 'speaking'].includes(S.voice.state);
  function syncSend() {
    $('scoreBtn').disabled = S.scoreBusy || S.tBusy || inCall() || !OPT.model_configured || S.test.filter((m) => m.from === 'user').length < 2;
    const mic = $('tMic');
    const label = inCall() ? 'End call' : S.voice.state === 'ended' ? 'Start another voice test' : 'Start voice test';
    mic.classList.toggle('on', inCall());
    mic.setAttribute('aria-label', label);
    $('tMicLabel').textContent = label;
    mic.disabled = !OPT.voice_configured || !S.agent || S.voice.state === 'connecting';
  }
  function renderScore() {
    const box = $('scorebox');
    if (S.scoreBusy) { box.hidden = false; box.textContent = ''; box.append(h('span', { class: 'eyebrow', text: 'SCORING AGAINST THE SKILLS…' })); return; }
    if (!S.score) { box.hidden = true; return; }
    box.hidden = false; box.textContent = '';
    box.append(h('span', { class: 'eyebrow', text: 'TEST SCORE' + (S.score.weighted_score != null ? ' · ' + S.score.weighted_score + ' / 5 WEIGHTED' : '') }));
    if (S.score.err) { box.append(h('p', { style: 'font-size:13px;color:var(--bad)', text: S.score.err })); return; }
    if (S.score.overall_text) box.append(h('p', { style: 'font-size:13.5px;color:var(--ink-2)', text: S.score.overall_text }));
    (S.score.scores || []).forEach((r) => {
      const n = r.score, cls = n == null ? 'na' : n >= 4 ? 'hi' : n >= 3 ? 'mid' : 'lo';
      box.append(h('div', { class: 'scr' }, h('span', { class: 'scp ' + cls, text: n == null ? '–' : n + '/5' }), h('div', null, h('b', { text: r.name }), h('span', { text: r.note }))));
    });
  }
  function renderStatus() {
    const v = S.voice, st = $('tStat');
    const name = S.agent ? S.agent.persona.name : 'Tara';
    const text = {
      connecting: 'Connecting the call…', live: 'Live call · speak normally', speaking: 'Live call · ' + name + ' is speaking',
      ended: 'Call ended · score it below, or start another', failed: v.err
    }[v.state] || '';
    st.textContent = text;
    st.className = 'tstat' + (v.state === 'live' || v.state === 'speaking' ? ' live' : v.state === 'failed' ? ' bad' : '');
  }
  function renderTest() {
    const log = $('tlog'); log.textContent = '';
    if (!S.test.length && !S.tBusy && !inCall()) {
      log.append(h('p', { class: 'empty', text: OPT.voice_configured
        ? 'Start a voice test to talk to ' + (S.agent ? S.agent.persona.name : 'Tara') + ' as a participant would. The transcript appears here as you speak.'
        : 'Voice testing is off until RETELL_API_KEY and RETELL_AGENT_BUILDER_AGENT_ID are set.' }));
    }
    const mmss = (x) => String(Math.floor(x / 60)).padStart(2, '0') + ':' + String(x % 60).padStart(2, '0');
    const who = S.agent ? S.agent.persona.name : 'Tara';
    S.test.forEach((m) => {
      if (m.from === 'err') { log.append(h('div', { class: 'eb', text: m.text })); return; }
      const meta = (m.from === 'tara' ? who : 'You') + (m.t != null ? ' · ' + mmss(m.t) : '');
      log.append(h('div', { class: 'tmeta ' + (m.from === 'tara' ? 'l' : 'r'), text: meta }), h('div', { class: m.from === 'tara' ? 'a' : 'u', text: m.text }));
    });
    if (S.voice.state === 'ended' && S.test.length) log.append(h('p', { class: 'empty', text: 'Call ended · ' + mmss(S.voice.dur || 0) + ' · transcript above. Score it below.' }));
    if (S.tBusy) log.append(h('span', { class: 'typing', 'aria-label': 'Typing' }, h('i'), h('i'), h('i')));
    log.scrollTop = log.scrollHeight;
    renderStatus(); renderScore(); syncSend();
  }
  async function scoreTest() {
    if (S.scoreBusy) return;
    S.scoreBusy = true; renderTest();
    try {
      S.score = await api('/agents/' + encodeURIComponent(S.agentId) + '/score', { method: 'POST', body: { transcript: turns() } });
    } catch (e) { S.score = { err: e.message }; }
    S.scoreBusy = false; renderTest();
  }
  $('tRestart').addEventListener('click', () => resetTest(true));
  $('scoreBtn').addEventListener('click', scoreTest);

  /* ---------- the real Retell call ---------- */
  async function startVoice() {
    const v = S.voice;
    // Ask for the microphone first, before a call is placed: a blocked mic is
    // the commonest failure, and it should not leave a dead call behind.
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
    } catch (e) {
      v.state = 'failed';
      v.err = e && e.name === 'NotAllowedError'
        ? 'This browser blocked the microphone. Allow it in the address bar, or open this page in Chrome: ' + location.href
        : 'No microphone was found. Connect one and try again.';
      renderTest(); return;
    }
    S.test = []; S.score = null; v.state = 'connecting'; v.err = ''; v.callId = ''; renderTest();
    try {
      await saveNow();
      const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/test-call', { method: 'POST' });
      v.callId = r.call_id;
      const mod = await import('https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3/+esm');
      const client = new mod.RetellWebClient();
      v.client = client;
      client.on('call_started', () => { v.state = 'live'; renderTest(); });
      client.on('agent_start_talking', () => { v.state = 'speaking'; renderStatus(); });
      client.on('agent_stop_talking', () => { if (v.state === 'speaking') v.state = 'live'; renderStatus(); });
      v.t0 = Date.now(); v.times = [];
      client.on('update', (u) => {
        if (!u || !Array.isArray(u.transcript)) return;
        const rows = u.transcript.filter((x) => x && x.content);
        while (v.times.length < rows.length) v.times.push(Math.round((Date.now() - v.t0) / 1000));
        S.test = rows.map((x, i) => ({ from: x.role === 'agent' ? 'tara' : 'user', text: x.content, t: v.times[i] }));
        renderTest();
      });
      client.on('call_ended', () => { v.state = 'ended'; v.client = null; v.dur = Math.round((Date.now() - v.t0) / 1000); renderTest(); });
      client.on('error', (err) => { v.state = 'failed'; v.err = 'The call dropped. ' + ((err && err.message) || ''); v.client = null; try { client.stopCall(); } catch (e) { /* gone */ } renderTest(); });
      await client.startCall({ accessToken: r.access_token });
    } catch (e) {
      v.state = 'failed'; v.err = e.message || 'The call couldn\'t start.'; v.client = null; renderTest();
    }
  }
  function endVoice() { const v = S.voice; if (v && v.client) { try { v.client.stopCall(); } catch (e) { /* gone */ } } }
  $('tMic').addEventListener('click', () => { if (inCall()) endVoice(); else startVoice(); });
  window.addEventListener('pagehide', endVoice);

  /* ---------- boot ---------- */
  (async function boot() {
    renderS1();
    try {
      OPT = await api('/options');
      const msgs = [];
      if (!OPT.model_configured) msgs.push('No model is configured, so drafts come from a template and Ask Tara, scoring are off.');
      if (!OPT.voice_configured) msgs.push('Voice testing is off until RETELL_API_KEY and RETELL_AGENT_BUILDER_AGENT_ID are set.');
      note(msgs.join(' '));
    } catch (e) { return; }
    loadMine();
    const id = new URLSearchParams(location.search).get('agent');
    if (id) {
      try { const row = await api('/agents/' + encodeURIComponent(id)); S.brief = row.brief || ''; $('brief').value = S.brief; S.mode = row.mode === 'assessment' ? 'assessment' : 'roleplay'; renderS1(); loadRow(row); go(3); }
      catch (e) { toast('That agent couldn\'t be opened.'); }
    }
  })();
})();
