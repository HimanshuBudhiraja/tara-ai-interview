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
    [1, 2, 3, 4].forEach((i) => { $('v' + i).hidden = i !== v; });
    if (v === 3) renderS3();
    if (v === 4) loadReport();
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
    S.askMsg = ''; S.editQ = -1; S.sessions = null;
    try { history.replaceState(null, '', '?agent=' + encodeURIComponent(row.agent_id)); } catch (e) { /* ignore */ }
    resetTest(false);
  }
  /* Purpose: who the result is for, and the attempt and feedback policy that follow. */
  const PURPOSES = [['Hiring', 'Hiring'], ['HR', 'HR'], ['L&D', 'Learning & Development']];
  const PURPOSE_DEFAULTS = { Hiring: { attempts: '1' }, HR: { attempts: '1' }, 'L&D': { attempts: 'Unlimited' } };
  const PURPOSE_HINT = {
    Hiring: 'A candidate, one attempt at a booked time. The result, with a recommendation, appears in Results for admins.',
    HR: 'An employee or manager conversation. Results show themes, observations and follow-ups for admins, not a verdict.',
    'L&D': 'A learner practising, with retries. Coaching and progress across attempts appear in Results for admins.'
  };
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
      { id: 'advanced', label: 'Advanced', group: 'OPTIONAL', opt: true },
      { id: 'results', label: 'Results' + (S.sessions && S.sessions.length ? ' (' + S.sessions.length + ')' : ''), group: 'AFTER PUBLISHING', opt: true, done: !!(S.sessions && S.sessions.some((x) => x.evaluation)) }
    ];
  }
  function renderSide() {
    const a = S.agent, side = $('side'); side.textContent = '';
    const items = readiness(), req = items.filter((i) => !i.opt), n = req.filter((i) => i.done).length;
    side.append(
      h('div', { class: 'sid' }, h('span', { class: 'bigava', text: initials(a.persona.name) }), h('div', null, h('b', { text: a.title }), h('small', { text: a.type_label }))),
      h('div', { class: 'ready' }, h('div', { class: 'top' }, h('span', { text: 'Ready to publish' }), h('b', { text: n + ' of ' + req.length })), h('div', { class: 'pbar' }, h('i', { style: 'width:' + Math.round(n / req.length * 100) + '%' })))
    );
    if (S.row && S.row.published_version) {
      const ol = S.row.open_link || {};
      side.append(h('div', { class: 'clink' },
        h('div', { class: 'top' }, h('span', { text: 'Participants' }), h('small', { text: 'v' + S.row.published_version })),
        h('p', { class: 'clstate', text: ol.enabled ? 'Open link is on: anyone with it can join.' : 'Open link is off. Invite people by email, or switch the open link on.' }),
        h('button', { class: 'obtn ghost', type: 'button', text: 'Invite participants', onclick: () => openInvite(ol.enabled ? 'link' : 'email') })));
    }
    ['BASICS', 'AGENT SETUP', 'OPTIONAL', 'AFTER PUBLISHING'].forEach((g) => {
      side.append(h('div', { class: 'ngroup' }, h('span', { text: g }), items.filter((i) => i.group === g).map((i) =>
        h('a', { class: 'navi', href: '#' + i.id, onclick: (e) => { e.preventDefault(); const t = $(i.id); if (t) { t.scrollIntoView({ behavior: 'smooth', block: 'start' }); t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash'); } } },
          i.label, i.review ? h('span', { class: 'revpill', text: 'Review' }) : i.done ? h('span', { class: 'okdot', 'aria-label': 'Complete', html: ICON.check11 }) : null))));
    });
    $('crumbT').textContent = a.title;
    const pub = S.row && S.row.published_version;
    $('dpill').textContent = pub ? 'PUBLISHED · v' + pub : 'DRAFT';
    $('dpill').className = 'draftpill' + (pub ? ' pub' : '');
    // Published: the header button duplicates instead of publishing again.
    $('pubBtn').textContent = S.row && S.row.locked ? 'Duplicate to edit' : 'Publish';
    $('invBtn').hidden = !pub;
    $('repBtn').hidden = !pub;
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
    if (!dirty || !S.agentId || (S.row && S.row.locked)) { dirty = false; return; }
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
      ),
      h('div', { class: 'segwrap', style: 'margin-top:4px' }, h('span', { class: 'lbl', text: 'Purpose' }),
        h('div', { role: 'group', 'aria-label': 'Purpose', class: 'seg' }, PURPOSES.map(([id, label]) => h('button', { type: 'button', 'aria-pressed': String(S.cfg.purpose === id), text: label,
          onclick: () => { S.cfg.purpose = id; Object.assign(S.cfg, PURPOSE_DEFAULTS[id]); touch(); renderMain(); } }))),
        h('small', { style: 'font-size:13px;color:var(--muted-2)', text: PURPOSE_HINT[S.cfg.purpose] || '' }))));

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
      h('div', { class: 'tt', style: 'display:flex;flex-direction:column;gap:6px' }, h('h2', { id: 'set-h', text: 'Conversation settings' }), h('p', { class: 'sub', text: 'How the conversation ends and how often one person may take it. The persona always opens.' })),
      srow('Ending', 'How the conversation wraps up.', 'ending', ['Tara decides', 'Hard time limit', 'No end time']),
      srow('Attempts', 'How many times one person may take it. Set by Purpose; change it here.', 'attempts', ['1', '3', 'Unlimited']),
      h('div', { class: 'srow', style: 'border-bottom:none;padding-bottom:0' },
        h('span', { class: 'tt' }, h('b', { text: 'Conversation length' }), h('small', { text: 'Set by Follow-up depth in Persona (' + c.depth + '). Change it there.' })),
        h('span', { style: 'display:flex;flex-direction:column;gap:4px' },
          h('span', { class: 'est' }, h('b', { text: '≈ ' + L.est }), h('span', { text: 'min' })),
          h('span', { class: 'estcalc', text: 'Call cap ' + L.cap + ' min (' + c.ending + ')' })))));

    const langs = (OPT.languages || []).length ? OPT.languages : [voiceOf(c.voice).language];
    m.append(h('section', { id: 'advanced', class: 'adv' },
      h('button', { type: 'button', 'aria-expanded': String(S.advOpen), onclick: () => { S.advOpen = !S.advOpen; renderMain(); } },
        h('span', null, h('b', { text: 'Advanced' }), h('small', { text: 'Language, proctoring, camera, recording consent' })), h('span', { class: 'spark', style: S.advOpen ? 'transform:rotate(180deg)' : null, html: ICON.chev })),
      S.advOpen ? h('div', { class: 'advbody' },
        h('div', { class: 'fld' }, h('label', { for: 'adv-lang', text: 'Language' }),
          h('select', { id: 'adv-lang', class: 'inp', onchange: (e) => { const v = (OPT.voices || []).find((x) => x.language === e.target.value); if (v) setVoice(v.key); } },
            langs.map((l) => h('option', { value: l, text: l, selected: voiceOf(c.voice).language === l }))),
          h('small', { class: 'advhint', text: 'Changing the language picks a voice that speaks it, and the persona takes that voice\'s name. Fine-tune the voice in Persona.' })),
        h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Proctoring' }), seg('Proctoring', 'proctoring', OPT.proctoring || ['Off', 'Basic', 'Strict']),
          h('small', { class: 'advhint', text: 'Saved with each published version for the proctoring suite, which applies it.' })),
        h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Camera' }), seg('Camera', 'camera', OPT.camera || ['Off', 'Optional', 'Required']),
          h('small', { class: 'advhint', text: 'Saved for the proctoring suite. Nothing is sent to the voice agent.' })),
        h('div', { class: 'togrow', style: 'grid-column:1 / -1' }, h('span', null, h('b', { text: 'Recording consent' }), h('small', { text: 'Ask participants to agree to recording before they start.' })),
          h('button', { class: 'sw', type: 'button', role: 'switch', 'aria-checked': String(c.consent), 'aria-label': 'Recording consent', onclick: () => { c.consent = !c.consent; touch(); renderMain(); } }, h('i')))) : null));

    m.append(resultsSection());
    if (S.row && S.row.locked) lockEditor(m);

    window.scrollTo(0, keepY);
  }
  /* ---------- Published = locked: usable (test, invite, results), never edited ---------- */
  function lockEditor(m) {
    m.querySelectorAll('input, textarea, select, button').forEach((el) => {
      if (el.closest('#results') || el.closest('#advanced > button') || el.closest('summary')) return;
      el.disabled = true;
    });
    m.querySelectorAll('[draggable]').forEach((el) => el.setAttribute('draggable', 'false'));
    m.prepend(h('div', { class: 'lockbar', role: 'status' },
      h('div', null, h('b', { text: 'Published · v' + S.row.published_version + ' · locked' }),
        h('span', { text: 'You can test it, invite people and read its reports. To change anything, duplicate it into a new draft.' })),
      h('button', { class: 'cwt', type: 'button', text: 'Duplicate to edit', onclick: duplicateAgent })));
  }
  async function duplicateAgent() {
    try {
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/duplicate', { method: 'POST' });
      loadRow(row); history.replaceState(null, '', '?agent=' + encodeURIComponent(row.agent_id));
      renderS3(); window.scrollTo(0, 0); loadMine();
      toast('Draft copy created. Edit it and publish it as a new role-play.');
    } catch (e) { toast(e.message); }
  }

  /* ---------- Reports: one grid per role-play, a report per attempt, admin notes and decision ---------- */
  $('repBtn').addEventListener('click', () => { S.rep = { q: '', status: 'all', decision: 'all', sort: 'ended_at', dir: -1, sel: null }; go(4); });
  $('repBack').addEventListener('click', () => go(3));
  $('repRefresh').addEventListener('click', () => loadReport());
  $('repCsv').addEventListener('click', exportCsv);
  async function loadReport() {
    S.rep = S.rep || { q: '', status: 'all', decision: 'all', sort: 'ended_at', dir: -1, sel: null };
    $('repBack').textContent = S.agent ? S.agent.title : 'Role-play';
    try { S.rep.data = await api('/agents/' + encodeURIComponent(S.agentId) + '/report'); } catch (e) { toast(e.message); return; }
    renderReport();
    if (S.rep.sel) openAttempt(S.rep.sel, true);
  }
  const mmssLong = (sec) => !sec ? '–' : Math.floor(sec / 60) + 'm ' + String(sec % 60).padStart(2, '0') + 's';
  const fmtDT = (t) => t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  function repRows() {
    const R = S.rep, q = R.q.trim().toLowerCase();
    let rows = R.data.rows.filter((x) => (!q || (x.name + ' ' + x.email).toLowerCase().includes(q))
      && (R.status === 'all' || x.status === R.status)
      && (R.decision === 'all' || (R.decision === 'none' ? !x.review.decision : x.review.decision === R.decision)));
    const key = (x) => R.sort.startsWith('skill:') ? x.skills[R.sort.slice(6)] : R.sort === 'decision' ? (x.review.decision || '') : x[R.sort];
    rows = rows.slice().sort((a, b) => { const va = key(a), vb = key(b); if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1; return (va > vb ? 1 : va < vb ? -1 : 0) * R.dir; });
    return rows;
  }
  function renderReport() {
    const R = S.rep, D = R.data, main = $('repMain'); main.textContent = '';
    const st = D.stats, skills = D.agent.skills;
    main.append(
      h('div', { class: 'reptitle' }, h('h1', { text: D.agent.title }),
        h('p', { text: D.agent.type_label + ' · ' + D.agent.purpose + ' · published v' + D.agent.published_version + ' · AI scores are backed by quotes; the decision column is yours.' })),
      h('div', { class: 'reptiles' }, [
        ['Attempts', st.attempts], ['People', st.people], ['Invited by email', st.invited], ['Evaluated', st.evaluated],
        ['Average score', st.average == null ? '–' : Math.round(st.average)], ['Reviewed', st.reviewed + ' of ' + st.attempts]
      ].map(([k, v]) => h('div', { class: 'reptile' }, h('span', { text: k }), h('b', { text: String(v) })))),
      h('div', { class: 'repskills', 'aria-label': 'Average by skill' }, skills.map((k) => h('span', null, k.name + ' · ' + Math.round(k.weight) + '%', h('b', { text: st.skills[k.name] == null ? '–' : String(Math.round(st.skills[k.name])) })))));
    const search = h('input', { type: 'search', placeholder: 'Search by name or email', value: R.q, 'aria-label': 'Search participants', oninput: (e) => { R.q = e.target.value; renderGrid(); } });
    const selStatus = h('select', { 'aria-label': 'Status', onchange: (e) => { R.status = e.target.value; renderGrid(); } },
      [['all', 'All statuses'], ['Evaluated', 'Evaluated'], ['Evaluating', 'Evaluating'], ['In progress', 'In progress'], ['Not evaluated', 'Not evaluated']].map(([v, l]) => h('option', { value: v, text: l, selected: R.status === v })));
    const selDec = h('select', { 'aria-label': 'Decision', onchange: (e) => { R.decision = e.target.value; renderGrid(); } },
      [['all', 'All decisions'], ['none', 'Not reviewed']].concat(D.decisions.map((d) => [d, d])).map(([v, l]) => h('option', { value: v, text: l, selected: R.decision === v })));
    main.append(h('div', { class: 'reptools' }, search, selStatus, selDec));
    const body = h('div', { class: 'repbody' + (R.sel ? ' open' : ''), id: 'repBody' }, h('div', { class: 'repgridwrap', id: 'repGridWrap' }), h('aside', { class: 'repdrawer', id: 'repDrawer', hidden: !R.sel, 'aria-label': 'Attempt report' }));
    main.append(body);
    renderGrid();
  }
  function renderGrid() {
    const R = S.rep, D = R.data, wrap = $('repGridWrap'); wrap.textContent = '';
    const rows = repRows();
    if (!rows.length) { wrap.append(h('div', { class: 'repempty', text: D.rows.length ? 'No attempts match these filters.' : 'No one has taken this role-play yet. Invite people from the role-play screen.' })); return; }
    const cols = [['name', 'Participant'], ['attempt', 'Attempt'], ['ended_at', 'Date'], ['duration_sec', 'Duration'], ['overall', 'Score'], ['band', 'Band'], ['ai_recommendation', 'AI recommendation']]
      .concat(D.agent.skills.map((k) => ['skill:' + k.name, k.name])).concat([['decision', 'Your decision'], ['notes', 'Notes']]);
    const head = h('tr', null, cols.map(([k, l]) => h('th', { scope: 'col', 'aria-sort': R.sort === k ? (R.dir > 0 ? 'ascending' : 'descending') : 'none',
      onclick: () => { if (k === 'notes') return; R.dir = R.sort === k ? -R.dir : (k === 'name' || k === 'band' ? 1 : -1); R.sort = k; renderGrid(); } }, l)));
    const decIdx = (d) => D.decisions.indexOf(d);
    const tb = h('tbody', null, rows.map((x) => h('tr', { 'aria-selected': String(R.sel === x.session_id), tabindex: '0',
      onclick: () => openAttempt(x.session_id), onkeydown: (e) => { if (e.key === 'Enter') openAttempt(x.session_id); } },
      h('td', null, h('b', { text: x.name || 'Participant' }), h('small', { text: x.email })),
      h('td', { class: 'num', text: String(x.attempt) }),
      h('td', { text: fmtDT(x.ended_at || x.started_at) }),
      h('td', { class: 'num', text: mmssLong(x.duration_sec) }),
      h('td', { class: 'num' }, x.overall == null ? h('span', { class: 'na', text: x.status }) : h('b', { text: String(Math.round(x.overall)) })),
      h('td', { text: x.band || '–' }),
      h('td', { class: 'rec', text: x.ai_recommendation || (x.problems.length ? 'Not evaluated: ' + x.problems.join('; ') : '–') }),
      D.agent.skills.map((k) => h('td', { class: 'num' + (x.skills[k.name] == null ? ' na' : ''), text: x.skills[k.name] == null ? (x.overall == null ? '–' : 'N/A') : String(x.skills[k.name]) })),
      h('td', null, x.review.decision ? h('span', { class: 'decpill d' + decIdx(x.review.decision), text: x.review.decision }) : h('span', { class: 'na', text: 'Not reviewed' })),
      h('td', { class: 'rec', text: x.review.notes ? (x.review.notes.length > 60 ? x.review.notes.slice(0, 60) + '…' : x.review.notes) : '' }))));
    wrap.append(h('table', { class: 'repgrid' }, h('thead', null, head), tb));
  }
  async function openAttempt(sid, keep) {
    const R = S.rep; R.sel = sid;
    $('repBody').className = 'repbody open';
    const dr = $('repDrawer'); dr.hidden = false; if (!keep) dr.textContent = '';
    renderGrid();
    let d;
    try { d = await api('/agents/' + encodeURIComponent(S.agentId) + '/attempts/' + encodeURIComponent(sid) + '/report'); } catch (e) { toast(e.message); return; }
    dr.textContent = '';
    const notes = h('textarea', { 'aria-label': 'Evaluation notes', placeholder: 'Your evaluation notes: what you saw, what to follow up, anything the score doesn\'t capture.', value: d.review.notes || '' });
    let decision = d.review.decision || '';
    const decSeg = h('div', { role: 'group', 'aria-label': 'Your decision', class: 'seg' });
    const paintDec = () => { decSeg.textContent = ''; R.data.decisions.forEach((o) => decSeg.append(h('button', { type: 'button', 'aria-pressed': String(decision === o), text: o, onclick: () => { decision = decision === o ? '' : o; paintDec(); } }))); };
    paintDec();
    const save = h('button', { class: 'cwt', type: 'button', text: 'Save review', onclick: async () => {
      save.disabled = true;
      try { const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/attempts/' + encodeURIComponent(sid) + '/review', { method: 'PUT', body: { decision, notes: notes.value } });
        const row = R.data.rows.find((x) => x.session_id === sid); if (row) row.review = r;
        toast('Review saved.'); await loadReport();
      } catch (e) { toast(e.message); }
      save.disabled = false;
    } });
    const meta = h('p', { class: 'meta', text: d.review.at ? 'Saved by ' + d.review.by + ' · ' + fmtDT(d.review.at) : 'Not reviewed yet' });
    dr.append(
      h('div', { style: 'display:flex;justify-content:space-between;gap:10px;align-items:flex-start' },
        h('div', null, h('h2', { text: (d.name || 'Participant') + (d.attempt > 1 ? ' · attempt ' + d.attempt : '') }), h('p', { class: 'meta', text: d.email })),
        h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Close report', html: ICON.x, onclick: () => { R.sel = null; $('repBody').className = 'repbody'; dr.hidden = true; renderGrid(); } })),
      d.evaluation ? h('div', { class: 'airec' }, h('b', { text: 'AI recommendation: ' }), d.evaluation.recommendation)
        : h('div', { style: 'display:flex;flex-direction:column;gap:8px;align-items:flex-start' },
          h('p', { class: 'meta', text: d.evaluation_error ? 'Not evaluated: ' + (d.evaluation_error.problems || []).join('; ') : d.status === 'complete' ? 'Not evaluated yet.' : 'This attempt is still in progress.' }),
          d.status === 'complete' ? h('button', { class: 'obtn ghost', type: 'button', text: 'Evaluate now', onclick: async (e) => {
            const b = e.currentTarget; b.disabled = true; b.textContent = 'Evaluating… (about a minute)';
            try { await api('/agents/' + encodeURIComponent(S.agentId) + '/attempts/' + encodeURIComponent(sid) + '/evaluate', { method: 'POST' }); toast('Evaluated.'); await loadReport(); }
            catch (x) { toast(x.message); b.disabled = false; b.textContent = 'Evaluate now'; }
          } }) : null),
      h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Your decision' }), decSeg),
      h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Evaluation notes' }), notes),
      h('div', { style: 'display:flex;gap:10px;align-items:center;flex-wrap:wrap' }, save, meta));
    if (d.evaluation) dr.append(resultView(d.evaluation));
    if ((d.transcript || []).length) {
      const who = S.agent ? S.agent.persona.name : 'Persona';
      dr.append(h('details', { class: 'resskill' }, h('summary', null, h('b', { text: 'Transcript (' + d.transcript.length + ' turns)' })),
        h('div', { class: 'reptx' }, d.transcript.map((t) => h('p', null, h('b', { text: (t.role === 'agent' ? who : d.name || 'Participant') + (t.t != null ? ' · ' + Math.floor(t.t / 60) + ':' + String(Math.round(t.t % 60)).padStart(2, '0') : '') + ': ' }), t.text)))));
    }
  }
  function exportCsv() {
    const D = S.rep && S.rep.data; if (!D) return;
    const esc = (v) => { const t = v == null ? '' : String(v); return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
    const head = ['Name', 'Email', 'Attempt', 'Status', 'Date', 'Duration (s)', 'Score', 'Band', 'AI recommendation'].concat(D.agent.skills.map((k) => k.name)).concat(['Decision', 'Notes', 'Reviewed by']);
    const lines = [head].concat(repRows().map((x) => [x.name, x.email, x.attempt, x.status, x.ended_at ? new Date(x.ended_at * 1000).toISOString() : '', x.duration_sec, x.overall, x.band, x.ai_recommendation]
      .concat(D.agent.skills.map((k) => x.skills[k.name])).concat([x.review.decision || '', x.review.notes || '', x.review.by || ''])));
    const blob = new Blob(['\ufeff' + lines.map((l) => l.map(esc).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = (D.agent.title || 'report').replace(/[^\w-]+/g, '_') + '_report.csv'; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  /* ---------- Results: every participant's attempts, with the evidence-backed report ---------- */
  async function loadResults() {
    if (!S.agentId) return;
    try { S.sessions = (await api('/agents/' + encodeURIComponent(S.agentId) + '/sessions')).sessions || []; } catch (e) { S.sessions = []; }
    if (S.view === 3) { renderMain(); renderSide(); }
  }
  function resultsSection() {
    const list = S.sessions || [];
    const sec = h('section', { id: 'results', class: 'card', 'aria-labelledby': 'res-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'res-h', text: 'Results' }),
        h('p', { class: 'sub', text: 'Every participant who has taken the published version. Scores are backed by quotes from what they said; skills the conversation didn\'t reach are Not assessed.' })),
        h('span', { style: 'display:flex;gap:8px' }, S.row && S.row.published_version ? h('button', { class: 'obtn ghost', type: 'button', text: 'Open reports', onclick: () => $('repBtn').click() }) : null,
          h('button', { class: 'obtn ghost', type: 'button', text: 'Refresh', onclick: loadResults }))));
    if (!list.length) { sec.append(h('p', { class: 'resnote', text: S.row && S.row.published_version ? 'No one has taken it yet. Share the candidate link from the sidebar.' : 'Publish this agent and share its link to collect results.' })); return sec; }
    const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
    list.forEach((x) => {
      const ev = x.evaluation;
      const status = ev ? (ev.overall == null ? 'Not rated' : Math.round(ev.overall) + ' · ' + ev.band)
        : x.status !== 'complete' ? 'In progress' : x.evaluation_error ? 'Not evaluated' : 'Evaluating…';
      const d = h('details', { class: 'resskill' },
        h('summary', null, h('span', { class: 'scp ' + (ev && ev.overall != null ? (ev.overall >= 70 ? 'hi' : ev.overall >= 50 ? 'mid' : 'lo') : 'na'), text: ev && ev.overall != null ? String(Math.round(ev.overall)) : '–' }),
          h('b', { text: (x.name || x.email || 'Participant') + (x.attempt > 1 ? ' · attempt ' + x.attempt : '') }),
          h('span', { class: 'resw', text: status + (x.ended_at ? ' · ' + fmtDate(x.ended_at) : '') })));
      if (ev) d.append(resultView(ev));
      else if (x.evaluation_error) d.append(h('p', { class: 'resnote', text: 'Not evaluated: ' + (x.evaluation_error.problems || []).join('; ') + '.' }));
      sec.append(d);
    });
    return sec;
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
  /* Participants only meet the persona: whoever wrote a line (Tara's draft or
   * a person typing), "Tara" becomes the persona's first name. The server does
   * the same on every save; this keeps the screen in step when a field is left. */
  function personaOnly() {
    const a = S.agent; if (!a) return false;
    const first = (a.persona.name || '').replace(/^(dr|mr|mrs|ms|prof)\.?\s+/i, '').split(/\s+/)[0] || 'the persona';
    let changed = false;
    const fix = (t) => { const n = (t || '').replace(/\bTara\b/g, first); if (n !== t) changed = true; return n; };
    ['title', 'description', 'instructions', 'opening_line', 'closing_line'].forEach((k) => { a[k] = fix(a[k]); });
    (a.questions || []).forEach((q) => { q.text = fix(q.text); });
    return changed;
  }
  document.addEventListener('focusout', (e) => {
    if (!S.agent || !e.target.closest || !e.target.closest('#v3') || !/^(TEXTAREA|INPUT)$/.test(e.target.tagName)) return;
    setTimeout(() => { if (personaOnly()) { touch(); renderMain(); } }, 0);
  });
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
  function renderS3() { if (!S.agent) { go(1); return; } renderSide(); renderMain(); syncTestHead(); renderTest(); loadResults(); }

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
    if (S.row && S.row.locked) { duplicateAgent(); return; }
    const missing = readiness().filter((i) => !i.opt && !i.done);
    if (missing.length) { toast('Finish ' + missing.map((i) => i.label.toLowerCase()).join(', ') + ' before publishing.'); const t = $(missing[0].id); if (t) t.scrollIntoView({ behavior: 'smooth' }); return; }
    try {
      await saveNow();
      const row = await api('/agents/' + encodeURIComponent(S.agentId) + '/publish', { method: 'POST' });
      S.row = row; renderSide(); toast('Published as version ' + row.published_version + '. The candidate link is in the sidebar.');
    } catch (e) {
      const d = e.detail || {};
      toast(d.missing ? 'Finish ' + d.missing.join(', ').toLowerCase() + ' first.' : d.leaked ? 'Reword this first: the instructions or questions repeat the "what a 5 looks like" text of a skill, which the voice agent must not see: "' + String(d.leaked[0]).slice(0, 90) + (String(d.leaked[0]).length > 90 ? '…' : '') + '"' : e.message);
    }
  });
  // One access code per participant, for the published version. The link opens the participant flow.
  $('invBtn').addEventListener('click', () => openInvite('email'));

  /* ---------- Invite participants: email an invitation, or switch the open link on ---------- */
  async function openInvite(tab) {
    S.inv = { tab: tab || 'email', list: null, last: null, busy: false };
    renderInvite(); $('invDlg').showModal(); loadInvites();
  }
  async function loadInvites() {
    try { const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/invites'); S.inv.list = r.invites; S.row.open_link = r.open_link; } catch (e) { S.inv.list = []; }
    renderInvite(); renderSide();
  }
  function copyBtn(text, label) {
    return h('button', { class: 'obtn ghost', type: 'button', text: label || 'Copy', onclick: (e) => { const b = e.currentTarget, was = b.textContent;
      navigator.clipboard.writeText(text).then(() => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = was; }, 1500); }, () => toast('Copy isn\'t allowed here. Select the text instead.')); } });
  }
  function renderInvite() {
    const st = S.inv, body = $('invBody'); body.textContent = '';
    $('invTitle').textContent = 'Invite participants · ' + S.agent.title;
    const tabs = $('invTabs'); tabs.textContent = '';
    [['email', 'Email invitation'], ['link', 'Open link']].forEach(([id, label]) => tabs.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(st.tab === id), text: label, onclick: () => { st.tab = id; renderInvite(); } })));
    if (st.tab === 'email') {
      const name = h('input', { id: 'inv-name', class: 'inp', type: 'text', placeholder: 'Maya Rao', autocomplete: 'off' });
      const mail = h('input', { id: 'inv-email', class: 'inp', type: 'email', placeholder: 'maya@company.com', autocomplete: 'off' });
      const note = h('textarea', { id: 'inv-note', class: 'inp', rows: '3', maxlength: '1500', placeholder: 'Optional: a line from you, added to the email' });
      const send = h('button', { class: 'cwt', type: 'button', text: OPT.email_configured ? 'Send invitation' : 'Create invitation', disabled: st.busy, onclick: async () => {
        if (!mail.value.trim()) { toast('Enter the participant\'s email address.'); mail.focus(); return; }
        st.busy = true; renderInviteBusy(send);
        try {
          st.last = await api('/agents/' + encodeURIComponent(S.agentId) + '/invites', { method: 'POST', body: { name: name.value.trim(), email: mail.value.trim(), message: note.value, send_email: !!OPT.email_configured } });
          toast(st.last.sent ? 'Invitation sent to ' + mail.value.trim() + '.' : 'Invitation created. Copy it below and send it yourself.');
          st.busy = false; await loadInvites(); return;
        } catch (e) { toast((e.detail && e.detail.message) || e.message); }
        st.busy = false; renderInvite();
      } });
      body.append(
        h('p', { class: 'invsub', text: 'Each person gets their own access code for the published version (v' + S.row.published_version + '). ' +
          (OPT.email_configured ? 'The invitation is emailed from ' + OPT.email_from + '.' : 'Email isn\'t set up on this server yet, so the invitation is created for you to copy and send yourself.') }),
        h('div', { class: 'invgrid' },
          h('div', { class: 'fld' }, h('label', { for: 'inv-name', text: 'Name' }), name),
          h('div', { class: 'fld' }, h('label', { for: 'inv-email', text: 'Email' }), mail),
          h('div', { class: 'fld', style: 'grid-column:1 / -1' }, h('label', { for: 'inv-note', text: 'Message' }), note)),
        h('div', { class: 'invact' }, send));
      if (st.last) {
        body.append(h('div', { class: 'invlast' },
          h('div', { class: 'top' }, h('b', { text: st.last.sent ? 'Sent' : 'Ready to send' }), st.last.email_error ? h('small', { text: st.last.email_error }) : null),
          h('pre', { class: 'invmail', text: 'Subject: ' + st.last.subject + '\n\n' + st.last.text }),
          h('div', { class: 'row' }, copyBtn(st.last.text, 'Copy invitation'), copyBtn(st.last.link, 'Copy link'), h('span', { class: 'code', text: st.last.code }))));
      }
      body.append(h('h3', { class: 'invh', text: 'Invited' }));
      if (st.list === null) body.append(h('p', { class: 'invsub', text: 'Loading…' }));
      else if (!st.list.length) body.append(h('p', { class: 'invsub', text: 'No one yet.' }));
      else body.append(h('div', { class: 'invlist' }, st.list.map((i) => h('div', { class: 'invrow' },
        h('div', null, h('b', { text: i.name || i.email || 'Participant' }), h('small', { text: (i.email || '') + (i.emailed ? ' · emailed' : '') })),
        h('span', { class: 'invst ' + i.status.toLowerCase().replace(/\s/g, ''), text: i.status }),
        h('span', { class: 'code', text: i.code }), copyBtn(i.link, 'Copy link')))));
    } else {
      const ol = S.row.open_link || {};
      const url = ol.enabled ? location.origin + ol.path : '';
      body.append(
        h('p', { class: 'invsub', text: 'One link anyone can use. Each person signs in with their own name and email, so their attempts and results stay separate. It\'s off until you switch it on; switching it off stops the link working at once.' }),
        h('div', { class: 'togrow' }, h('span', null, h('b', { text: 'Open link' }), h('small', { text: ol.enabled ? 'On: anyone with the link can join.' : 'Off' })),
          h('button', { class: 'sw', type: 'button', role: 'switch', 'aria-checked': String(!!ol.enabled), 'aria-label': 'Open link', onclick: async () => {
            try { const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/open-link', { method: 'POST', body: { enabled: !ol.enabled } }); S.row.open_link = r; toast(r.enabled ? 'Open link is on.' : 'Open link is off. The link no longer works.'); }
            catch (e) { toast(e.message); }
            renderInvite(); renderSide();
          } }, h('i'))));
      if (ol.enabled) body.append(h('div', { class: 'invlast' },
        h('a', { href: url, target: '_blank', rel: 'noopener', text: url.replace(/^https?:\/\//, '') }),
        h('div', { class: 'row' }, h('span', { class: 'code', text: ol.code }), copyBtn(url, 'Copy link'))));
    }
  }
  function renderInviteBusy(btn) { btn.disabled = true; btn.textContent = OPT.email_configured ? 'Sending…' : 'Creating…'; }
  $('invClose').addEventListener('click', () => $('invDlg').close());

  /* ---------- Help ---------- */
  document.querySelectorAll('[data-help]').forEach((b) => b.addEventListener('click', () => $('helpDlg').showModal()));
  $('helpClose').addEventListener('click', () => $('helpDlg').close());
  document.querySelectorAll('[data-home]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); go(1); window.scrollTo(0, 0); }));

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
    const left = S.voice.left != null ? ' · ' + String(Math.floor(S.voice.left / 60)) + ':' + String(S.voice.left % 60).padStart(2, '0') + ' left' : '';
    const label = inCall() ? 'End call' + left : S.voice.state === 'ended' ? 'Start another voice test' : 'Start voice test';
    mic.classList.toggle('on', inCall());
    mic.setAttribute('aria-label', label);
    $('tMicLabel').textContent = label;
    mic.disabled = !OPT.voice_configured || !S.agent || S.voice.state === 'connecting';
  }
  function renderScore() {
    const box = $('scorebox');
    if (S.scoreBusy) { box.hidden = false; box.textContent = ''; box.append(h('span', { class: 'eyebrow', text: 'EVALUATING AGAINST THE SKILLS…' })); return; }
    if (!S.score) { box.hidden = true; return; }
    box.hidden = false; box.textContent = '';
    if (S.score.err) { box.append(h('span', { class: 'eyebrow', text: 'TEST RESULT' }), h('p', { style: 'font-size:13px;color:var(--bad)', text: S.score.err })); return; }
    box.append(resultView(S.score, { compact: true }));
  }
  /* One rendering of an evaluation, used by the test panel and the Results page. */
  function resultView(r, opts) {
    opts = opts || {};
    const ev = {}; (r.evidence || []).forEach((e) => { ev[e.id] = e; });
    const mmss = (x) => x == null ? '' : String(Math.floor(x / 60)).padStart(2, '0') + ':' + String(Math.round(x % 60)).padStart(2, '0');
    const wrap = h('div', { class: 'resv' });
    wrap.append(h('div', { class: 'reshead' },
      h('div', null, h('span', { class: 'eyebrow', text: (opts.compact ? 'TEST RESULT · ' : '') + (r.purpose || '').toUpperCase() }),
        h('div', { class: 'resbig' }, h('b', { text: r.overall == null ? '—' : String(Math.round(r.overall)) }), h('span', { text: r.overall == null ? 'not rated' : '/ 100 · ' + r.band }))),
      h('p', { class: 'resrec', text: r.recommendation })));
    if (r.weight_coverage != null && r.weight_coverage < 1) wrap.append(h('p', { class: 'resnote', text: Math.round(r.weight_coverage * 100) + '% of the skill weight was assessed. Skills the conversation didn\'t reach are marked Not assessed, not scored 0.' }));
    (r.skills || []).forEach((sk) => {
      const na = sk.status !== 'ASSESSED';
      const cls = na ? 'na' : sk.score >= 70 ? 'hi' : sk.score >= 50 ? 'mid' : 'lo';
      const row = h('details', { class: 'resskill' },
        h('summary', null, h('span', { class: 'scp ' + cls, text: na ? 'N/A' : String(sk.score) }), h('b', { text: sk.name }),
          h('span', { class: 'resw', text: na ? 'Not assessed' : (sk.weight != null ? Math.round(sk.weight) + '% weight' : '') })));
      if (na) row.append(h('p', { class: 'resnote', text: 'Not assessed: ' + (sk.reason || 'not enough evidence in the conversation') + '.' }));
      else {
        if (sk.rationale) row.append(h('p', { class: 'resnote', text: sk.rationale }));
        const crit = Object.entries(sk.criteria || {}).filter(([, v]) => v != null);
        if (crit.length) row.append(h('div', { class: 'rescrit' }, crit.map(([k, v]) => h('span', { text: k + ' ' + v + '/5' }))));
        (sk.evidence_ids || []).map((i) => ev[i]).filter(Boolean).forEach((e) => row.append(h('blockquote', { class: 'resq ' + e.polarity },
          h('span', { class: 'resqmeta', text: (e.t != null ? mmss(e.t) + ' · ' : '') + e.criterion + ' · ' + e.polarity }), '“' + e.quote + '”', e.why ? h('small', { text: e.why }) : null)));
      }
      wrap.append(row);
    });
    const N = r.narrative || {};
    const LISTS = [['did_well', 'What went well'], ['improve', 'What to improve'], ['try_next', 'Try next time'], ['strengths', 'Strengths'], ['development_areas', 'Development areas'], ['themes', 'Themes'], ['observations', 'Observations'], ['follow_ups', 'Follow-ups']];
    if (N.summary) wrap.append(h('p', { class: 'resnote', text: N.summary }));
    LISTS.forEach(([k, label]) => { if ((N[k] || []).length) wrap.append(h('div', { class: 'reslist' }, h('b', { text: label }), h('ul', null, N[k].map((it) => h('li', { text: it.text }))))); });
    if (r.versions) wrap.append(h('p', { class: 'resver', text: 'Scenario ' + r.versions.scenario + ' · rubric ' + r.versions.rubric + (r.versions.flow ? ' · flow ' + r.versions.flow : '') + ' · ' + r.versions.evaluation }));
    return wrap;
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
        ? 'Start a voice test to talk to ' + (S.agent ? S.agent.persona.name : 'the persona') + ' as a participant would. Tests are real calls on the live voice agent, limited to ' + (OPT.test_call_minutes || 5) + ' minutes. The transcript appears here as you speak.'
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
    } catch (e) { S.score = { err: (e.detail && e.detail.message) || e.message }; }
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
      client.on('call_started', () => {
        v.state = 'live'; v.left = (r.max_minutes || OPT.test_call_minutes || 5) * 60;
        clearInterval(v.tick); v.tick = setInterval(() => { v.left = Math.max(0, v.left - 1); syncSend(); if (!inCall()) { clearInterval(v.tick); v.left = null; } }, 1000);
        renderTest();
      });
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
      client.on('call_ended', () => { clearInterval(v.tick); v.left = null; v.state = 'ended'; v.client = null; v.dur = Math.round((Date.now() - v.t0) / 1000); renderTest(); });
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
