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
    { cat: 'ai', kind: 'Role-play', title: 'AI Engineer Technical Conversation', desc: 'ML fundamentals, RAG vs fine-tuning, MLOps and responsible AI for mid-to-senior engineers.', mins: '25 min', format: 'Voice' },
    { cat: 'ai', kind: 'Role-play', title: 'Prompt Engineer Screening', desc: 'Prompt design, evaluation methods and failure analysis with live scenario questions.', mins: '20 min', format: 'Voice' },
    { cat: 'ai', kind: 'Role-play', title: 'ML Ops Engineer Deep-Dive', desc: 'Pipelines, drift monitoring, model serving and rollback strategy for production ML.', mins: '30 min', format: 'Voice' },
    { cat: 'ai', kind: 'Role-play', title: 'Data Scientist Case Study', desc: 'The participant walks through a churn-prediction case: framing, features, metrics, trade-offs.', mins: '30 min', format: 'Voice' },
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
    { label: 'Senior AI Engineer technical conversation', text: 'A 25-minute technical conversation for a senior AI Engineer at DeepMind. Probe ML fundamentals, RAG vs fine-tuning, MLOps and responsible AI. Be friendly but rigorous.', mode: 'roleplay' },
    { label: 'SDR cold-call roleplay', text: 'A cold-call roleplay where Tara is a busy VP of Operations at a logistics company. The rep must book a 30-minute demo. Push back on timing twice.', mode: 'roleplay' },
    { label: 'Support de-escalation practice', text: 'An angry customer whose order arrived damaged for the second time. Score empathy, ownership and resolution. 10 minutes, voice.', mode: 'roleplay' }
  ];
  /* Assessment is on hold: shown, but it can't be chosen anywhere. */
  const MODES = [['roleplay', 'Role-play'], ['assessment', 'Assessment']];
  function renderS1() {
    const md = $('modes'); md.textContent = '';
    S.mode = 'roleplay';
    MODES.forEach(([id, label]) => md.append(id === 'assessment'
      ? h('button', { type: 'button', class: 'held', disabled: true, 'aria-disabled': 'true', title: 'Assessment is coming soon' }, label, h('span', { class: 'soon', text: 'Soon' }))
      : h('button', { type: 'button', 'aria-pressed': String(S.mode === id), text: label, onclick: () => { S.mode = id; renderS1(); } })));
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
    S.mode = 'roleplay';
    $('brief').value = S.brief; renderS1(); build();
  }
  $('brief').addEventListener('input', (e) => { S.brief = e.target.value; syncCreate(); });
  $('brief').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !$('create').disabled) { e.preventDefault(); build(); } });
  $('create').addEventListener('click', build);
  /* The top tabs: the one you click is highlighted (and follows the scroll). */
  function markNav(id) { ['navAgents', 'navMine', 'navTpl'].forEach((k) => $(k).classList.toggle('on', k === id)); }
  $('navTpl').addEventListener('click', (e) => { e.preventDefault(); markNav('navTpl'); $('templates').scrollIntoView({ behavior: 'smooth' }); });
  $('navMine').addEventListener('click', (e) => { e.preventDefault(); markNav('navMine'); $('mine').scrollIntoView({ behavior: 'smooth' }); });
  $('navAgents').addEventListener('click', () => markNav('navAgents'));
  window.addEventListener('scroll', () => {
    if (S.view !== 1 && S.view != null) return;
    const y = window.scrollY + 140, mine = $('mine'), tpl = $('templates');
    markNav(tpl.offsetTop && y >= tpl.offsetTop ? 'navTpl' : !mine.hidden && y >= mine.offsetTop ? 'navMine' : 'navAgents');
  }, { passive: true });
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
      S.brief = row.brief || ''; $('brief').value = S.brief; S.mode = 'roleplay';
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
        h('span', { class: 'tr' }, h('span', { class: 'cat ' + (pub ? 'pub' : 'draft'), text: pub ? 'Published' : 'Draft', title: pub ? 'Version ' + a.published_version : 'Not published yet' }), h('span', { class: 'kind', text: a.type_label })),
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
      const detail = i === 0 && S.ctxNote ? S.ctxNote : i === 1 ? ([F.type, F.role, F.difficulty].filter(Boolean).join(' · ') || 'Type, role and difficulty') : s.detail;
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
    S.building = true; S.stage = 0; S.buildErr = ''; S.pf = S.pa = S.pc = null; S.ctxNote = '';
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
          if (ev === 'context') { S.ctxNote = data.evaluation_chars ? 'Split your brief: ' + data.persona_chars.toLocaleString() + ' characters for the persona, ' + data.evaluation_chars.toLocaleString() + ' for scoring only.' : ''; }
          else if (ev === 'plan') { S.stage = 1; S.pf = data.fields; S.pa = data.agent; if (data.drafted_by === 'template') note('No model is configured, so Tara filled this draft from a template. Edit it on the next screen.'); }
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
      { id: 'knowledge', label: 'Scenario knowledge', group: 'OPTIONAL', opt: true, done: !!((a.context || '').trim() || (a.additional_context || '').trim() || (a.evaluation_context || '').trim()) },
      { id: 'questions', label: 'Potential AI Questions', group: 'OPTIONAL', opt: true, done: a.questions.length > 0 },
      { id: 'exhibits', label: 'Exhibits', group: 'OPTIONAL', opt: true, done: (a.exhibits || []).length > 0 },
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
        h('div', { class: 'top' }, h('span', { text: 'Participants' })),
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
    $('dpill').textContent = pub ? 'PUBLISHED' : 'DRAFT';
    $('dpill').title = pub ? 'Version ' + pub : 'Not published yet';
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

    const HELD = { Assessment: 'Assessment (coming soon)' };
    const sel = (id, key, opts, obj) => h('select', { id, class: 'inp', onchange: bind(obj || S.fields, key, () => { renderSide(); }) }, opts.map((o) =>
      h('option', { value: o, text: HELD[o] || o, disabled: !!HELD[o], selected: (obj || S.fields)[key] === o || null })));
    const typeOpts = ['Role-play', 'Assessment'];
    m.append(h('section', { id: 'scenario', class: 'card', 'aria-labelledby': 'sc-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'sc-h', text: 'Scenario details' }), h('p', { class: 'sub', text: 'Tara filled these from your brief. Change any of them here.' }))),
      h('div', { class: 'grid4' },
        h('div', { class: 'fld' }, h('label', { for: 'f-type', text: 'Scenario type' }), sel('f-type', 'type', typeOpts)),
        h('div', { class: 'fld' }, h('label', { for: 'f-role', text: 'Role or situation' }), h('input', { id: 'f-role', class: 'inp', type: 'text', value: S.fields.role || '', oninput: bind(S.fields, 'role') })),
        h('div', { class: 'fld', style: 'grid-column:1 / -1' }, h('label', { for: 'f-skills', text: 'What to assess' }), h('input', { id: 'f-skills', class: 'inp', type: 'text', value: S.fields.skills || '', oninput: bind(S.fields, 'skills') }))
      ),
      ));

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
        h('div', { class: 'fld' }, h('label', { for: 'close-t', text: 'Closing line' }),
          h('textarea', { id: 'close-t', class: 'inp', rows: '3', value: a.closing_line, oninput: (e) => { bind(a, 'closing_line')(e); $('closeWarn').hidden = !/\?/.test(e.target.value); } }),
          h('small', { id: 'closeWarn', class: 'advhint', style: 'color:#B54708', hidden: !/\?/.test(a.closing_line || ''),
            text: 'The call ends right after the closing line, so a question here would be asked and then hung up on. Questions are removed when you save; the persona already asks "anything else?" in the wrap-up and waits for the answer.' })))));

    const ctxBox = (id, key, label, hint, max, rows) => {
      const cnt = h('small', { class: 'advhint', style: 'text-align:right;margin-top:0' });
      const upd = () => { const n = (a[key] || '').length; cnt.textContent = n.toLocaleString() + ' / ' + max.toLocaleString(); cnt.style.color = n > max ? '#B42318' : ''; };
      upd();
      return h('div', { class: 'fld' }, h('label', { for: id, text: label }),
        h('textarea', { id, class: 'inp', rows: String(rows), maxlength: String(max), value: a[key] || '', placeholder: hint, oninput: (e) => { bind(a, key)(e); upd(); } }),
        h('div', { style: 'display:flex;justify-content:space-between;gap:12px' }, h('small', { class: 'advhint', style: 'margin-top:0', text: '' }), cnt));
    };
    m.append(h('section', { id: 'knowledge', class: 'card', style: 'gap:16px', 'aria-labelledby': 'kn-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'kn-h', text: 'Scenario knowledge' }), h('p', { class: 'sub', text: 'The detail from your brief, kept word for word. Long briefs are split so the persona never sees the answer key.' })), h('span', { class: 'privpill' }, h('span', { class: 'spark', html: ICON.eyeoff }), 'Private')),
      ctxBox('kn-ctx', 'context', 'Scenario context (the persona plays from this)', 'Role and company context, products in scope, situations, what to probe, objections to raise.', 10000, 8),
      ctxBox('kn-add', 'additional_context', 'Additional context (facts the persona may share when asked)', 'e.g. pricing, policies, product facts. If a participant asks about something not here, the persona says it doesn\'t have that detail.', 3000, 4),
      ctxBox('kn-eval', 'evaluation_context', 'Evaluation guidance (used for scoring only)', 'Expected answers, strong versus weak answers, accuracy rules, what to reward or penalise.', 10000, 8),
      h('small', { class: 'advhint', style: 'margin-top:-6px', text: 'Evaluation guidance is never sent to the voice agent. Publishing is blocked if any of its sentences appear in the instructions, context or questions.' })));

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

    m.append(exhibitsSection(a));
    const srow = (title, hint, key, opts) => h('div', { class: 'srow' }, h('span', { class: 'tt' }, h('b', { text: title }), h('small', { text: hint })), seg(title, key, opts));
    const L = lengthParts();
    m.append(h('section', { id: 'settings', class: 'card', style: 'gap:22px', 'aria-labelledby': 'set-h' },
      h('div', { class: 'tt', style: 'display:flex;flex-direction:column;gap:6px' }, h('h2', { id: 'set-h', text: 'Conversation settings' }), h('p', { class: 'sub', text: 'How the conversation ends and how often one person may take it. The persona always opens.' })),
      srow('Ending', 'How the conversation wraps up.', 'ending', ['Tara decides', 'Hard time limit', 'No end time']),
      srow('Attempts', 'How many times one person may take it.', 'attempts', ['1', '3', 'Unlimited']),
      h('div', { class: 'srow', style: 'border-bottom:none;padding-bottom:0' },
        h('span', { class: 'tt' }, h('b', { text: 'Conversation length' }), h('small', { text: 'Set by Follow-up depth in Persona (' + c.depth + '). Change it there.' })),
        h('span', { style: 'display:flex;flex-direction:column;gap:4px' },
          h('span', { class: 'est' }, h('b', { text: '≈ ' + L.est }), h('span', { text: 'min' })),
          h('span', { class: 'estcalc', text: 'Call cap ' + L.cap + ' min (' + c.ending + ')' })))));

    const langs = (OPT.languages || []).length ? OPT.languages : [voiceOf(c.voice).language];
    m.append(h('section', { id: 'advanced', class: 'adv' },
      h('button', { type: 'button', 'aria-expanded': String(S.advOpen), onclick: () => { S.advOpen = !S.advOpen; renderMain(); } },
        h('span', null, h('b', { text: 'Advanced' }), h('small', { text: 'Language, proctoring defaults, recording consent' })), h('span', { class: 'spark', style: S.advOpen ? 'transform:rotate(180deg)' : null, html: ICON.chev })),
      S.advOpen ? h('div', { class: 'advbody' },
        h('div', { class: 'fld' }, h('label', { for: 'adv-lang', text: 'Language' }),
          h('select', { id: 'adv-lang', class: 'inp', onchange: (e) => { const v = (OPT.voices || []).find((x) => x.language === e.target.value); if (v) setVoice(v.key); } },
            langs.map((l) => h('option', { value: l, text: l, selected: voiceOf(c.voice).language === l }))),
          h('small', { class: 'advhint', text: 'Changing the language picks a voice that speaks it, and the persona takes that voice\'s name. Fine-tune the voice in Persona.' })),
        h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Image proctoring (default)' }), yesNo('adv-img', !!c.image_proctoring, (v) => { c.image_proctoring = v; touch(); }),
          h('small', { class: 'advhint', text: 'Default for new invitations; each invitation can change it. Applied by the proctoring suite.' })),
        h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Safe Assessment Browser (default)' }), yesNo('adv-sab', !!c.safe_browser, (v) => { c.safe_browser = v; touch(); }),
          h('small', { class: 'advhint', text: 'Default for new invitations; each invitation can change it. Applied by the proctoring suite.' })),
        h('div', { class: 'togrow', style: 'grid-column:1 / -1' }, h('span', null, h('b', { text: 'Recording consent' }), h('small', { text: 'Ask participants to agree to recording before they start.' })),
          h('button', { class: 'sw', type: 'button', role: 'switch', 'aria-checked': String(c.consent), 'aria-label': 'Recording consent', onclick: () => { c.consent = !c.consent; touch(); renderMain(); } }, h('i')))) : null));

    m.append(resultsSection());
    if (S.row && S.row.locked) lockEditor(m);

    window.scrollTo(0, keepY);
  }
  /* Exhibit charts as SVG text (bar, line or pie), one drawing shared by the builder
   * preview and the participant's screen. Labels are escaped; numbers are numbers. */
  function chartSVG(c) {
    const esc = (t) => String(t).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    const L = (c && c.labels) || [], V = ((c && c.values) || []).map((v) => +v || 0), unit = c && c.unit ? ' ' + c.unit : '';
    if (!L.length) return '';
    const W = 560, H = 260, pal = ['#7F56D9', '#2E90FA', '#12B76A', '#F79009', '#F04438', '#9E77ED', '#06AED4', '#EE46BC', '#667085', '#15B79E', '#FB6514', '#6172F3'];
    const fmt = (v) => (Math.round(v * 100) / 100).toLocaleString() + unit;
    if (c.type === 'pie') {
      const tot = V.reduce((a, b) => a + Math.max(0, b), 0) || 1; let a0 = -Math.PI / 2; const cx = 130, cy = 130, r = 105;
      const slices = V.map((v, i) => { const a1 = a0 + Math.max(0, v) / tot * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0;
        const p = V.length === 1 ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${pal[i % 12]}"/>` :
          `<path d="M${cx},${cy} L${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)} A${r},${r} 0 ${big} 1 ${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)} Z" fill="${pal[i % 12]}" stroke="#fff" stroke-width="2"/>`;
        a0 = a1; return p; }).join('');
      const legend = L.map((l, i) => `<g transform="translate(280,${24 + i * 20})"><rect width="12" height="12" rx="2" fill="${pal[i % 12]}"/><text x="18" y="10.5" font-size="12.5" fill="currentColor">${esc(l)}: ${esc(fmt(V[i]))} (${Math.round(Math.max(0, V[i]) / tot * 100)}%)</text></g>`).join('');
      return `<svg viewBox="0 0 ${W} ${Math.max(H, 40 + L.length * 20)}" role="img" style="width:100%;height:auto;font-family:inherit">${slices}${legend}</svg>`;
    }
    const pad = { l: 52, r: 16, t: 18, b: 44 }, pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const max = Math.max(0, ...V), min = Math.min(0, ...V), span = (max - min) || 1;
    const y = (v) => pad.t + ph - (v - min) / span * ph, step = pw / L.length;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + span * f);
    let g = ticks.map((t) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}" stroke="currentColor" stroke-opacity=".12"/><text x="${pad.l - 8}" y="${y(t) + 4}" text-anchor="end" font-size="11" fill="currentColor" fill-opacity=".6">${esc(fmt(t))}</text>`).join('');
    g += L.map((l, i) => `<text x="${pad.l + step * i + step / 2}" y="${H - pad.b + 18}" text-anchor="middle" font-size="11.5" fill="currentColor" fill-opacity=".75">${esc(l.length > 14 ? l.slice(0, 13) + '…' : l)}</text>`).join('');
    if (c.type === 'line') {
      const pts = V.map((v, i) => `${pad.l + step * i + step / 2},${y(v)}`);
      g += `<polyline points="${pts.join(' ')}" fill="none" stroke="#7F56D9" stroke-width="2.5"/>` + V.map((v, i) => `<circle cx="${pad.l + step * i + step / 2}" cy="${y(v)}" r="4" fill="#7F56D9"/><text x="${pad.l + step * i + step / 2}" y="${y(v) - 9}" text-anchor="middle" font-size="11" fill="currentColor">${esc(fmt(v))}</text>`).join('');
    } else {
      const bw = Math.min(56, step * 0.6);
      g += V.map((v, i) => { const x = pad.l + step * i + (step - bw) / 2, top = Math.min(y(v), y(0)), hgt = Math.abs(y(v) - y(0));
        return `<rect x="${x}" y="${top}" width="${bw}" height="${Math.max(1, hgt)}" rx="4" fill="${pal[i % 12]}"/><text x="${x + bw / 2}" y="${top - 6}" text-anchor="middle" font-size="11" fill="currentColor">${esc(fmt(v))}</text>`; }).join('');
    }
    return `<svg viewBox="0 0 ${W} ${H}" role="img" style="width:100%;height:auto;font-family:inherit">${g}</svg>`;
  }

  /* ---------- Exhibits: charts and images the participant sees during the conversation ---------- */
  function exhibitsSection(a) {
    a.exhibits = a.exhibits || [];
    const add = (kind) => { if (a.exhibits.length >= 6) { toast('Up to 6 exhibits.'); return; }
      a.exhibits.push(kind === 'chart' ? { kind, title: 'Exhibit ' + (a.exhibits.length + 1), description: '', chart: { type: 'bar', labels: ['Q1', 'Q2', 'Q3'], values: [0, 0, 0], unit: '' } }
        : { kind, title: 'Exhibit ' + (a.exhibits.length + 1), description: '', file: '' }); touch(); renderMain(); };
    const sec = h('section', { id: 'exhibits', class: 'card', 'aria-labelledby': 'exh-h' },
      h('div', { class: 'ch2' }, h('div', { class: 'tt' }, h('h2', { id: 'exh-h', text: 'Exhibits' }),
        h('p', { class: 'sub', text: 'Charts or images the participant sees during the conversation. The persona refers to them by name ("take a look at Exhibit 1") and they open on the participant\'s screen. The persona only knows what you write in "What the persona knows".' }))));
    a.exhibits.forEach((e, i) => {
      const up = (k, v) => { e[k] = v; touch(); };
      const preview = h('div', { class: 'exprev' });
      const paint = () => { preview.textContent = ''; if (e.kind === 'chart') preview.innerHTML = chartSVG(e.chart) || '<p class="resnote">Add at least one row.</p>';
        else if (e.file) preview.append(h('img', { src: API + '/agents/' + encodeURIComponent(S.agentId) + '/exhibit-images/' + e.file, alt: e.title }));
        else preview.append(h('p', { class: 'resnote', text: 'No image yet.' })); };
      const body = h('div', { class: 'exbody' });
      if (e.kind === 'chart') {
        const c = e.chart;
        const rows = h('div', { class: 'exrows' });
        const paintRows = () => { rows.textContent = ''; c.labels.forEach((lab, r) => rows.append(h('div', { class: 'exrow' },
          h('input', { class: 'inp', type: 'text', value: lab, maxlength: '30', 'aria-label': 'Label ' + (r + 1), oninput: (ev) => { c.labels[r] = ev.target.value; touch(); paint(); } }),
          h('input', { class: 'inp', type: 'number', step: 'any', value: String(c.values[r]), 'aria-label': 'Value ' + (r + 1), oninput: (ev) => { c.values[r] = +ev.target.value || 0; touch(); paint(); } }),
          h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Remove row ' + (r + 1), html: ICON.x, onclick: () => { c.labels.splice(r, 1); c.values.splice(r, 1); touch(); paintRows(); paint(); } })))); };
        paintRows();
        body.append(
          h('div', { class: 'exgrid' },
            h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Chart type' }), h('div', { role: 'group', 'aria-label': 'Chart type', class: 'seg' }, [['bar', 'Bar'], ['line', 'Line'], ['pie', 'Pie']].map(([v, l]) =>
              h('button', { type: 'button', 'aria-pressed': String(c.type === v), text: l, onclick: (ev) => { c.type = v; touch(); ev.currentTarget.parentElement.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === ev.currentTarget))); paint(); } })))),
            h('div', { class: 'fld' }, h('label', { text: 'Unit (optional)' }), h('input', { class: 'inp', type: 'text', value: c.unit, maxlength: '12', placeholder: 'e.g. $k, %, hrs', oninput: (ev) => { c.unit = ev.target.value; touch(); paint(); } }))),
          h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Data (label and value)' }), rows,
            h('button', { class: 'dashbtn ghost', type: 'button', text: '+ Add row', onclick: () => { if (c.labels.length >= 12) { toast('Up to 12 rows.'); return; } c.labels.push(''); c.values.push(0); touch(); paintRows(); paint(); } })));
      } else {
        const file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', hidden: true, onchange: async (ev) => {
          const f = ev.target.files[0]; if (!f) return;
          if (f.size > 5 * 1024 * 1024) { toast('Use an image up to 5 MB.'); return; }
          const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
          try { await saveNow(); const out = await api('/agents/' + encodeURIComponent(S.agentId) + '/exhibit-images', { method: 'POST', body: { data_base64: data } }); e.file = out.file; dirty = true; await saveNow(); paint(); toast('Image uploaded.'); }
          catch (x) { toast(x.message); }
        } });
        body.append(file, h('button', { class: 'obtn ghost', type: 'button', text: e.file ? 'Replace image' : 'Upload image (PNG, JPEG or WebP, up to 5 MB)', onclick: () => file.click() }));
      }
      paint();
      sec.append(h('div', { class: 'exitem' },
        h('div', { class: 'exhead' }, h('b', { text: 'Exhibit ' + (i + 1) + ' · ' + (e.kind === 'chart' ? 'Chart' : 'Image') }),
          h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Remove exhibit ' + (i + 1), html: ICON.x, onclick: () => { a.exhibits.splice(i, 1); touch(); renderMain(); } })),
        h('div', { class: 'exgrid' },
          h('div', { class: 'fld' }, h('label', { text: 'Title (the participant sees it)' }), h('input', { class: 'inp', type: 'text', value: e.title, maxlength: '80', oninput: (ev) => { up('title', ev.target.value); } })),
          h('div', { class: 'fld' }, h('label', { text: 'What the persona knows' }), h('textarea', { class: 'ed', rows: '3', maxlength: '600', value: e.description, placeholder: 'What this shows and what matters in it. The persona only talks about it using this (and the chart data).', oninput: (ev) => { up('description', ev.target.value); } }))),
        body, preview));
    });
    sec.append(h('div', { class: 'rbtns' },
      h('button', { class: 'dashbtn ghost', type: 'button', text: '+ Add chart', disabled: a.exhibits.length >= 6, onclick: () => add('chart') }),
      h('button', { class: 'dashbtn ghost', type: 'button', text: '+ Add image', disabled: a.exhibits.length >= 6, onclick: () => add('image') })));
    return sec;
  }

  /* ---------- Published = locked: usable (test, invite, results), never edited ---------- */
  function lockEditor(m) {
    m.querySelectorAll('input, textarea, select, button').forEach((el) => {
      if (el.closest('#results') || el.closest('#advanced > button') || el.closest('summary')) return;
      el.disabled = true;
    });
    m.querySelectorAll('[draggable]').forEach((el) => el.setAttribute('draggable', 'false'));
    m.prepend(h('div', { class: 'lockbar', role: 'status' },
      h('div', null, h('b', { text: 'Published · locked' }),
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

  /* ---------- Reports: all role-plays, or one; Figma layout in the builder's purple ---------- */
  const SVGI = {
    search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
    download: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M4 20h16"/></svg>',
    filter: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 4h18l-7 9v6l-4 2v-8z"/></svg>',
    report: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 20h16M7 17V11M11 17V7M15 17v-4M19 17V9"/></svg>',
    info: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    back: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 6l-6 6 6 6"/></svg>',
    invite: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="10" cy="8" r="4"/><path d="M3 20c1-4 4-6 7-6s6 2 7 6M19 8v6M16 11h6"/></svg>',
    prev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 6l-6 6 6 6"/></svg>',
    next: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9 6l6 6-6 6"/></svg>',
    positive: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg>',
    caution: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9.5"/><path d="M12 7v6M12 16.5h.01"/></svg>',
    negative: '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9.5"/><path d="M9 9l6 6M15 9l-6 6"/></svg>'
  };
  const fmtDT = (t) => t ? new Date(t * 1000).toLocaleString([], { day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '–';
  function openReports(scope) {
    S.rep = { scope, data: null, q: '', status: '', rec: '', agent: '', page: 0, per: 50, filtersOpen: false };
    $('subRoleplays').classList.toggle('on', false); $('subReports').classList.toggle('on', true);
    go(4);
  }
  $('repBtn').addEventListener('click', () => openReports('agent'));
  $('subReports').addEventListener('click', (e) => { e.preventDefault(); openReports('all'); });
  document.querySelectorAll('[data-home]').forEach((a) => a.addEventListener('click', () => { $('subReports').classList.remove('on'); $('subRoleplays').classList.add('on'); }));
  async function loadReport() {
    const R = S.rep || (S.rep = { scope: 'agent', q: '', status: '', rec: '', agent: '', page: 0, per: 50 });
    try { R.data = R.scope === 'all' ? await api('/reports') : await api('/agents/' + encodeURIComponent(S.agentId) + '/report'); }
    catch (e) { toast(e.message); return; }
    renderReport();
  }
  const activeFilters = () => [S.rep.status, S.rep.rec, S.rep.agent].filter(Boolean).length;
  function repRows() {
    const R = S.rep, q = R.q.trim().toLowerCase();
    return R.data.rows.filter((x) => (!q || (x.name + ' ' + x.email).toLowerCase().includes(q))
      && (!R.status || x.status === R.status) && (!R.rec || x.recommendation_level === R.rec) && (!R.agent || x.agent_id === R.agent));
  }
  function renderReport() {
    const R = S.rep, D = R.data, main = $('repMain'); main.textContent = '';
    const all = R.scope === 'all';
    if (!all) main.append(h('button', { class: 'repback', type: 'button', html: SVGI.back + '<span>Back to role-play</span>', onclick: () => { $('subReports').classList.remove('on'); $('subRoleplays').classList.add('on'); go(3); } }));
    const done = D.rows.filter((x) => x.overall != null);
    const avg = done.length ? Math.round(done.reduce((t, x) => t + x.overall, 0) / done.length) : null;
    main.append(h('div', { class: 'rephead' }, h('h1', { id: 'repH', text: all ? 'Role-play Reports' : 'Reports' }),
      h('p', { text: all ? 'Every conversation across your published role-plays. Open a report for the evidence, your notes and your recommendation.'
        : 'Role: ' + (D.agent.role || D.agent.title) + ' · ' + D.agent.title + (avg != null ? ' · average score ' + avg : '') })));
    main.append(h('hr', { class: 'repdivide' }));
    const search = h('input', { type: 'search', placeholder: 'Search by participant', value: R.q, 'aria-label': 'Search by participant', oninput: (e) => { R.q = e.target.value; R.page = 0; renderTable(); } });
    const fbtn = h('button', { class: 'iconbtn', type: 'button', title: 'Filters', 'aria-label': 'Filters', 'aria-expanded': String(!!R.filtersOpen), html: SVGI.filter, onclick: () => { R.filtersOpen = !R.filtersOpen; renderReport(); } });
    if (activeFilters()) fbtn.append(h('span', { class: 'badge', text: String(activeFilters()) }));
    main.append(h('div', { class: 'reptools' },
      h('label', { class: 'repsearch' }, h('span', { html: SVGI.search }), search),
      h('div', { class: 'r' },
        !all ? h('button', { class: 'invoutline', type: 'button', html: SVGI.invite + '<span>Invite participants</span>', onclick: () => openInvite('email') }) : null,
        h('button', { class: 'iconbtn', type: 'button', title: 'Download Excel', 'aria-label': 'Download Excel', html: SVGI.download, onclick: () => $('xlsDlg').showModal() }),
        fbtn)));
    if (R.filtersOpen) {
      const sel = (label, key, opts) => h('select', { 'aria-label': label, onchange: (e) => { R[key] = e.target.value; R.page = 0; renderReport(); } }, opts.map(([v, l]) => h('option', { value: v, text: l, selected: R[key] === v })));
      main.append(h('div', { class: 'repfilters' },
        sel('Status', 'status', [['', 'All statuses'], ['Completed', 'Completed'], ['In Progress', 'In Progress'], ['Terminated', 'Terminated'], ['Pending', 'Pending']]),
        sel('Recommendation', 'rec', [['', 'All recommendations'], ['positive', 'Recommended'], ['caution', 'Needs review'], ['negative', 'Not recommended']]),
        all ? sel('Role-play', 'agent', [['', 'All role-plays']].concat(D.agents.map((a) => [a.agent_id, a.title]))) : null,
        activeFilters() ? h('button', { class: 'clear', type: 'button', text: 'Clear filters', onclick: () => { R.status = R.rec = R.agent = ''; renderReport(); } }) : null));
    }
    main.append(h('div', { class: 'repgridwrap', id: 'repGridWrap' }), h('div', { class: 'repfoot', id: 'repFoot' }));
    renderTable();
  }
  function recCell(x) {
    if (!x.recommendation_level) return h('span', { class: 'na', text: '-' });
    const who = x.recommendation_source === 'admin' ? 'Your decision: ' : 'AI recommendation: ';
    return h('span', { class: 'recicon ' + x.recommendation_level + (x.recommendation_source === 'admin' ? ' admin' : ''), title: who + x.recommendation, 'aria-label': who + x.recommendation, html: SVGI[x.recommendation_level] });
  }
  function renderTable() {
    const R = S.rep, all = R.scope === 'all', wrap = $('repGridWrap'), foot = $('repFoot');
    wrap.textContent = ''; foot.textContent = '';
    const rows = repRows(), total = rows.length, pages = Math.max(1, Math.ceil(total / R.per));
    R.page = Math.min(R.page, pages - 1);
    const page = rows.slice(R.page * R.per, R.page * R.per + R.per);
    if (!total) wrap.append(h('div', { class: 'repempty', text: R.data.rows.length ? 'No reports match your search or filters.' : (all ? 'No one has taken a published role-play yet.' : 'No one has been invited yet. Use Invite participants to send invitations.') }));
    else {
      const th = (label, info) => h('th', { scope: 'col' }, label, info ? h('span', { class: 'info', title: info, html: SVGI.info }) : null);
      const head = h('tr', null, th(all ? 'Participant' : 'Participant Name'), all ? th('Role-play') : null, th('Date'), th('Status'), th('Score'),
        th('Proctoring Details', 'Proctoring settings for this role-play. The proctoring suite applies them and adds its findings.'),
        th('Recommendation', 'Your decision when you have reviewed the attempt; otherwise the AI recommendation, backed by quotes in the report.'), th('Action'));
      const body = h('tbody', null, page.map((x) => h('tr', null,
        h('td', null, h('span', { class: 'cand' }, all ? h('b', { text: x.email || x.name || 'Invited · ' + (x.invite_code || '') }) : h('b', { text: x.name || x.email || 'Invited · ' + (x.invite_code || '') }), h('small', { text: all ? x.name : x.email }))),
        all ? h('td', { text: x.agent_title }) : null,
        h('td', { text: fmtDT(x.date) }),
        h('td', null, h('span', { class: 'st ' + x.status.toLowerCase().replace(/\s/g, ''), text: x.status })),
        h('td', { text: x.overall == null ? (x.evaluation === 'Evaluating' ? '…' : '-') : String(Math.round(x.overall)) }),
        h('td', { text: x.proctoring && x.proctoring !== 'Off' ? x.proctoring : '-' }),
        h('td', null, recCell(x)),
        h('td', null, h('span', { class: 'acts' },
          h('button', { class: 'iconbtn', type: 'button', title: 'View report', 'aria-label': 'View report for ' + (x.name || x.email), html: SVGI.report, disabled: !x.session_id, onclick: () => openAttempt(x.agent_id, x.session_id) }),
          h('button', { class: 'iconbtn', type: 'button', title: 'Download report', 'aria-label': 'Download report for ' + (x.name || x.email), html: SVGI.download, disabled: !x.session_id, onclick: () => printAttempt(x.agent_id, x.session_id) }))))));
      wrap.append(h('table', { class: 'repgrid' }, h('thead', null, head), body));
    }
    const from = total ? R.page * R.per + 1 : 0, to = Math.min(total, (R.page + 1) * R.per);
    foot.append(
      h('span', null, 'Total Record Count: ', h('b', { text: String(total) })),
      h('select', { 'aria-label': 'Records per page', onchange: (e) => { R.per = +e.target.value; R.page = 0; renderTable(); } }, [10, 25, 50, 100].map((n) => h('option', { value: n, text: String(n), selected: R.per === n }))),
      h('span', { text: 'Records per page' }),
      h('button', { class: 'pg', type: 'button', 'aria-label': 'Previous page', html: SVGI.prev, disabled: R.page === 0, onclick: () => { R.page--; renderTable(); } }),
      h('span', { text: from + ' - ' + to }),
      h('button', { class: 'pg', type: 'button', 'aria-label': 'Next page', html: SVGI.next, disabled: R.page >= pages - 1, onclick: () => { R.page++; renderTable(); } }));
  }
  /* Download Excel: the server builds a real .xlsx with the current filters. */
  function banner(kind, text) {
    const b = $('dlBanner'); b.className = 'dlbanner ' + kind; b.textContent = text; b.hidden = false;
    clearTimeout(b._t); if (kind !== 'busy') b._t = setTimeout(() => { b.hidden = true; }, 3500);
  }
  $('xlsClose').addEventListener('click', () => $('xlsDlg').close());
  $('xlsCancel').addEventListener('click', () => $('xlsDlg').close());
  $('xlsGo').addEventListener('click', async () => {
    $('xlsDlg').close();
    const R = S.rep, qs = new URLSearchParams({ q: R.q, status: R.status, rec: R.rec, agent: R.agent });
    const url = API + (R.scope === 'all' ? '/reports.xlsx?' : '/agents/' + encodeURIComponent(S.agentId) + '/report.xlsx?') + qs;
    banner('busy', 'Downloading report…');
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('status ' + r.status);
      const blob = await r.blob();
      const name = ((/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '') || [])[1]) || 'reports.xlsx';
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
      banner('ok', 'Report downloaded successfully');
    } catch (e) { banner('bad', 'Couldn\'t download the report. Try again.'); }
  });
  /* One attempt: report, the admin's decision and notes, transcript. */
  function closeDrawer() { $('repDrawer').hidden = true; $('drawerBack').hidden = true; }
  $('drawerBack').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('repDrawer').hidden) closeDrawer(); });
  async function fetchAttempt(agentId, sid) {
    return api('/agents/' + encodeURIComponent(agentId) + '/attempts/' + encodeURIComponent(sid) + '/report');
  }
  async function openAttempt(agentId, sid) {
    const dr = $('repDrawer'); dr.textContent = ''; dr.hidden = false; $('drawerBack').hidden = false;
    dr.append(h('p', { class: 'meta', text: 'Loading…' }));
    let d; try { d = await fetchAttempt(agentId, sid); } catch (e) { toast(e.message); closeDrawer(); return; }
    dr.textContent = '';
    const notes = h('textarea', { 'aria-label': 'Evaluation notes', placeholder: 'Your evaluation notes: what you saw, what to follow up, anything the score doesn\'t capture.', value: d.review.notes || '' });
    let decision = d.review.decision || '';
    const decSeg = h('div', { role: 'group', 'aria-label': 'Your recommendation', class: 'seg' });
    const paint = () => { decSeg.textContent = ''; d.decisions.forEach((o) => decSeg.append(h('button', { type: 'button', 'aria-pressed': String(decision === o), text: o, onclick: () => { decision = decision === o ? '' : o; paint(); } }))); };
    paint();
    const save = h('button', { class: 'cwt', type: 'button', text: 'Save', onclick: async () => {
      save.disabled = true;
      try { await api('/agents/' + encodeURIComponent(agentId) + '/attempts/' + encodeURIComponent(sid) + '/review', { method: 'PUT', body: { decision, notes: notes.value } });
        toast('Saved.'); closeDrawer(); await loadReport(); }
      catch (e) { toast(e.message); save.disabled = false; }
    } });
    dr.append(
      h('div', { style: 'display:flex;justify-content:space-between;gap:10px;align-items:flex-start' },
        h('div', null, h('h2', { text: (d.name || 'Participant') + (d.attempt > 1 ? ' · attempt ' + d.attempt : '') }), h('p', { class: 'meta', text: d.email + ' · ' + d.agent_title })),
        h('button', { class: 'xbtn', type: 'button', 'aria-label': 'Close report', html: ICON.x, onclick: closeDrawer })),
      d.evaluation ? h('div', { class: 'airec' }, h('b', { text: 'AI recommendation: ' }), d.evaluation.recommendation) :
        h('div', { style: 'display:flex;flex-direction:column;gap:8px;align-items:flex-start' },
          h('p', { class: 'meta', text: d.evaluation_error ? 'Not evaluated: ' + (d.evaluation_error.problems || []).join('; ') : d.status === 'complete' ? 'Not evaluated yet.' : 'This attempt is still in progress.' }),
          d.status === 'complete' ? h('button', { class: 'obtn ghost', type: 'button', text: 'Evaluate now', onclick: async (e) => {
            const b = e.currentTarget; b.disabled = true; b.textContent = 'Evaluating… (about a minute)';
            try { await api('/agents/' + encodeURIComponent(agentId) + '/attempts/' + encodeURIComponent(sid) + '/evaluate', { method: 'POST' }); toast('Evaluated.'); openAttempt(agentId, sid); loadReport(); }
            catch (x) { toast(x.message); b.disabled = false; b.textContent = 'Evaluate now'; }
          } }) : null),
      h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Your recommendation' }), decSeg),
      h('div', { class: 'fld' }, h('span', { class: 'lbl', text: 'Evaluation notes' }), notes),
      h('div', { style: 'display:flex;gap:10px;align-items:center;flex-wrap:wrap' }, save,
        h('button', { class: 'obtn ghost', type: 'button', text: 'Download report', onclick: () => printAttempt(agentId, sid) }),
        h('span', { class: 'meta', text: d.review.at ? 'Last saved by ' + d.review.by + ' · ' + fmtDT(d.review.at) : 'Not reviewed yet' })));
    dr.append(reportDoc(d));
  }
  /* The participant report, laid out like the AI conversation report PDF: header,
   * skill summary with 1-5 stars and AI notes, strengths and areas of improvement,
   * recommendation, the timed transcript, disclaimer. Used in the panel and for download. */
  function reportDoc(d) {
    const ev = d.evaluation || {}, N = ev.narrative || {};
    const lvl = { positive: 'positive', caution: 'caution', negative: 'negative' };
    const recLevel = (() => { const r = (ev.recommendation || '').toLowerCase(); return r.startsWith('recommended') || r.startsWith('proceed') ? 'positive' : r.startsWith('not ') ? 'negative' : r ? 'caution' : ''; })();
    const recPill = (txt, level) => h('span', { class: 'rpill ' + (lvl[level] || 'caution') }, h('span', { html: SVGI[level || 'caution'] }), txt);
    // Level 1-5 from the Total Skill Score (/25); older results without a level fall back to the 0-100 score.
    const levelOf = (k) => k.status !== 'ASSESSED' ? 0 : k.level || Math.max(1, Math.round((k.score || 0) / 20));
    const stars = (n, label) => h('span', { class: 'stars', 'aria-label': n ? n + ' of 5' + (label ? ', ' + label : '') : 'Not discussed' },
      [1, 2, 3, 4, 5].map((i) => h('span', { class: i <= n ? 'on' : '', text: '★' })), label ? h('em', { text: label }) : null);
    const mmss = (x) => x == null ? '' : String(Math.floor(x / 60)).padStart(2, '0') + ':' + String(Math.round(x % 60)).padStart(2, '0');
    const name = d.name || d.email || 'Participant', persona = d.persona || 'Persona';
    const strengths = (N.strengths || N.did_well || []).map((x) => x.text);
    const improve = (N.improve || N.development_areas || []).concat(N.next_steps || N.try_next || []).map((x) => x.text);
    const doc = h('article', { class: 'rdoc' },
      h('h1', { text: 'AI Conversation Report' }),
      h('div', { class: 'rwho' }, h('span', { class: 'rava', text: (name[0] || 'P').toUpperCase() }), h('b', { text: name })),
      h('div', { class: 'rmeta' },
        h('div', null, h('small', { text: 'AI Conversation Name' }), h('b', { text: d.agent_title }), h('span', { text: d.email || '' })),
        h('div', null, h('small', { text: 'Recommendation' }), ev.recommendation ? recPill(ev.recommendation, recLevel) : h('b', { text: 'Not evaluated' })),
        h('div', null, h('small', { text: 'Conversation date' }), h('b', { text: d.date ? new Date(d.date * 1000).toLocaleDateString([], { day: 'numeric', month: 'long', year: 'numeric' }) : '–' })),
        h('div', null, h('small', { text: 'Conversation language' }), h('b', { text: d.language || '–' })),
        h('div', null, h('small', { text: 'Invitation type' }), h('b', { text: d.invitation_type || '–' }))));
    if (d.evaluation) {
      doc.append(h('h2', { text: 'Skill Summary' + (ev.overall != null ? ' · overall ' + Math.round(ev.overall) + ' / 100' : '') }),
        h('div', { class: 'rtablewrap' }, h('table', { class: 'rtable' },
          h('thead', null, h('tr', null, h('th', { text: 'Skill' }), h('th', { text: 'Level (1-5)' }), h('th', null, h('span', { class: 'spark', text: '✦ ' }), 'AI Evaluation Notes'))),
          h('tbody', null, (ev.skills || []).map((k) => h('tr', null,
            h('td', null, k.name, k.discussion === 'mentioned' ? h('span', { class: 'chipm', text: 'Mentioned' }) : null),
            h('td', null, stars(levelOf(k), k.level_label || '')),
            h('td', { text: k.status === 'ASSESSED' ? (k.rationale || '—') : 'Not discussed in the conversation.' })))))),
        h('div', { class: 'rcols' },
          h('section', null, h('h3', null, h('span', { class: 'ok', html: SVGI.positive }), 'Strengths'), h('div', { class: 'rbox ok' }, strengths.length ? h('ul', null, strengths.map((t) => h('li', { text: t }))) : h('p', { text: 'None identified from the evidence.' }))),
          h('section', null, h('h3', null, h('span', { class: 'warn', html: SVGI.caution }), 'Areas of Improvement'), h('div', { class: 'rbox warn' }, improve.length ? h('ul', null, improve.map((t) => h('li', { text: t }))) : h('p', { text: 'None identified from the evidence.' })))),
        h('div', { class: 'rrec' }, h('h2', null, 'Recommendation ', recPill(ev.recommendation, recLevel)), N.summary ? h('p', { text: N.summary }) : null));
      // Per-skill detail cards: level, total out of 25, the five criteria, and the questions asked with the answers given.
      const turns = d.transcript || [], pIdx = {}; let pn = 0;
      turns.forEach((t, i) => { if (t.role === 'user') { pn++; pIdx['P' + pn] = i; } });
      const evById = {}; (ev.evidence || []).forEach((e) => { evById[e.id] = e; });
      const cards = (ev.skills || []).filter((k) => k.status === 'ASSESSED').map((k) => {
        const qa = []; const seen = new Set();
        (k.evidence_ids || []).map((i) => evById[i]).filter(Boolean).forEach((e) => {
          const at = pIdx[e.turn]; if (at == null || seen.has(at)) return; seen.add(at);
          let q = ''; for (let j = at - 1; j >= 0; j--) { if (turns[j].role === 'agent') { q = turns[j].text; break; } }
          qa.push({ q, a: turns[at].text, t: turns[at].t, quote: e.quote, polarity: e.polarity });
        });
        return h('section', { class: 'rcard' },
          h('div', { class: 'rcardh' }, h('b', { text: k.name }), stars(levelOf(k), k.level_label || ''),
            h('span', { class: 'rtot', text: (k.total != null ? k.total : Math.round((k.score || 0) / 4)) + ' / 25' }),
            k.discussion === 'mentioned' ? h('span', { class: 'chipm', text: 'Mentioned' }) : null),
          Object.keys(k.criteria || {}).length ? h('div', { class: 'rcrit' }, Object.entries(k.criteria).map(([c, v]) => h('span', null, c, h('b', { text: ' ' + (v == null ? '–' : v + '/5') })))) : null,
          qa.length ? h('ol', { class: 'rqa' }, qa.map((x) => h('li', null,
            h('p', { class: 'q' }, h('b', { text: persona + ': ' }), x.q || '—'),
            h('p', { class: 'a' }, h('b', { text: name + (x.t != null ? ' · ' + mmss(x.t) : '') + ': ' }), x.a)))) : h('p', { class: 'meta', text: 'No question and answer evidence recorded for this skill.' }));
      });
      if (cards.length) doc.append(h('h2', { text: 'Skill Details' }), h('div', { class: 'rcards' }, cards));
    } else {
      doc.append(h('p', { class: 'meta', text: d.evaluation_error ? 'Not evaluated: ' + (d.evaluation_error.problems || []).join('; ') : 'Not evaluated yet.' }));
    }
    if (d.review && (d.review.decision || d.review.notes)) doc.append(h('div', { class: 'rreview' }, h('h3', { text: 'Reviewer' }),
      d.review.decision ? h('p', null, h('b', { text: 'Recommendation: ' }), d.review.decision) : null, d.review.notes ? h('p', { text: d.review.notes }) : null,
      d.review.by ? h('small', { text: 'By ' + d.review.by + (d.review.at ? ' · ' + new Date(d.review.at * 1000).toLocaleString() : '') }) : null));
    if ((d.transcript || []).length) {
      const audio = h('div', { class: 'raudio', 'data-audio': '' });
      const seek = (sec) => { const a = audio.querySelector('audio'); if (a && sec != null) { a.currentTime = sec; a.play().catch(() => {}); } };
      doc.append(h('h2', { text: 'Audio and Transcriptions' }), audio, h('div', { class: 'rtx' }, d.transcript.map((t) => {
        const ai = t.role === 'agent', who = ai ? persona : name;
        return h('div', { class: 'rmsg' + (ai ? '' : ' me') }, h('div', { class: 'rmh' }, h('span', { class: 'rav' + (ai ? ' ai' : ''), text: ai ? '✦' : (who[0] || 'P').toUpperCase() }), h('b', { text: who })),
          h('p', { text: t.text }), t.t != null ? h('button', { type: 'button', class: 'rts', title: 'Play from here', text: mmss(t.t), onclick: () => seek(t.t) }) : null);
      })));
      if (d.agent_id && d.session_id) loadAudio(d.agent_id, d.session_id, audio);
    }
    doc.append(h('div', { class: 'rdisc' }, h('h3', { text: 'Disclaimer' }), h('p', { text: 'This automated evaluation is based on the participant\'s responses during the AI conversation. We recommend a follow-up conversation with a person to validate these findings and explore anything that needs a closer look.' })));
    return doc;
  }
  async function loadAudio(agentId, sid, box) {
    box.append(h('p', { class: 'meta', text: 'Loading the recording…' }));
    let r; try { r = await api('/agents/' + encodeURIComponent(agentId) + '/attempts/' + encodeURIComponent(sid) + '/audio'); } catch (e) { r = { recordings: [] }; }
    box.textContent = '';
    if (!r.recordings.length) { box.append(h('p', { class: 'meta', text: 'No recording is available for this conversation.' })); return; }
    r.recordings.forEach((x) => box.append(h('div', { class: 'raudrow' },
      r.recordings.length > 1 ? h('small', { text: 'Part ' + x.part + ' (reconnected)' }) : null,
      h('audio', { controls: true, preload: 'none', src: x.url, 'aria-label': 'Recording' + (r.recordings.length > 1 ? ', part ' + x.part : '') }))));
    if (r.recordings.length > 1) box.append(h('p', { class: 'meta', text: 'Timestamps play from the first part.' }));
  }
  function transcriptView(d) {
    return h('details', { class: 'resskill' }, h('summary', null, h('b', { text: 'Transcript (' + d.transcript.length + ' turns)' })),
      h('div', { class: 'reptx' }, d.transcript.map((t) => h('p', null, h('b', { text: (t.role === 'agent' ? d.persona || 'Persona' : d.name || 'Participant') + (t.t != null ? ' · ' + Math.floor(t.t / 60) + ':' + String(Math.round(t.t % 60)).padStart(2, '0') : '') + ': ' }), t.text))));
  }
  /* Download one report: a print-ready page (Save as PDF from the print dialog). */
  async function printAttempt(agentId, sid) {
    let d; try { d = await fetchAttempt(agentId, sid); } catch (e) { toast(e.message); return; }
    const w = window.open('', '_blank'); if (!w) { toast('Allow pop-ups for this page to download the report.'); return; }
    const box = h('div', { class: 'printdoc' }, reportDoc(d));
    box.querySelectorAll('details').forEach((x) => x.setAttribute('open', ''));
    const css = [...document.querySelectorAll('style')].map((x) => x.textContent).join('\n');
    w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' + (d.name || 'Participant').replace(/</g, '') + ' · AI Conversation Report</title><style>' + css +
      ' body{padding:32px;max-width:860px;margin:0 auto}.printdoc h1{font-size:22px;margin:0 0 6px}@media print{body{padding:0}}</style></head><body></body></html>');
    w.document.body.append(w.document.importNode(box, true)); w.document.close();
    setTimeout(() => { w.focus(); w.print(); }, 300);
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
    if (!list.length) { sec.append(h('p', { class: 'resnote', text: S.row && S.row.published_version ? 'No one has taken it yet. Invite participants from the sidebar or the header.' : 'Publish this agent and share its link to collect results.' })); return sec; }
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
    ['title', 'opening_line', 'closing_line', 'instructions', 'description', 'context', 'additional_context'].forEach((k) => { const t = swap(a[k] || ''); if (t !== a[k]) { a[k] = t; changed++; } });
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
    ['title', 'description', 'instructions', 'opening_line', 'closing_line', 'context', 'additional_context'].forEach((k) => { a[k] = fix(a[k]); });
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
      S.row = row; renderSide(); toast('Published. Invite participants from the sidebar or the header.');
    } catch (e) {
      const d = e.detail || {};
      toast(d.missing ? 'Finish ' + d.missing.join(', ').toLowerCase() + ' first.' : d.leaked ? 'Reword this first: the instructions, context or questions repeat scoring-only text (a skill\'s "what a 5 looks like" or the evaluation guidance), which the voice agent must not see: "' + String(d.leaked[0]).slice(0, 90) + (String(d.leaked[0]).length > 90 ? '…' : '') + '"' : e.message);
    }
  });
  // One access code per participant, for the published version. The link opens the participant flow.
  $('invBtn').addEventListener('click', () => openInvite('email'));

  /* ---------- Send Invitation: email up to ten people, or the open link ---------- */
  const COIN = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="9" cy="9" r="6"/><path d="M15.5 9.6A6 6 0 1 1 9.6 15.5"/><path d="M9 6.5v5"/></svg>';
  const LINKI = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/></svg>';
  async function openInvite(tab) {
    S.inv = { tab: tab || 'email', data: null, sent: null, busy: false };
    $('invDlg').showModal(); renderInvite();
    try { S.inv.data = await api('/agents/' + encodeURIComponent(S.agentId) + '/invitation'); }
    catch (e) { toast(e.message); $('invDlg').close(); return; }
    const d = S.inv.data;
    S.inv.email = { text: '', p: Object.assign({}, d.defaults) };
    S.inv.link = { enabled: d.open_link.enabled, p: Object.assign({}, d.open_link.proctoring || d.defaults) };
    renderInvite();
  }
  const parseEmails = (t) => t.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
  const okEmail = (e) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e);
  function info(text) { return h('span', { class: 'ii', title: text, 'aria-label': text, html: SVGI.info }); }
  function yesNo(name, value, onchange) {
    return h('div', { class: 'yn', role: 'radiogroup', 'aria-label': name }, [[true, 'Yes'], [false, 'No']].map(([v, l]) =>
      h('label', null, h('input', { type: 'radio', name: name, checked: value === v, onchange: () => onchange(v) }), h('span', { text: l }))));
  }
  function proctorBlock(key, st) {
    return h('div', { class: 'invsec' }, h('h3', { text: 'Proctoring Settings' }),
      h('div', { class: 'procgrid' },
        h('div', null, h('span', { class: 'lbl' }, 'Image Proctoring', info('Saved with each attempt for the proctoring suite, which captures and reviews images. Nothing changes in the conversation.')),
          yesNo(key + '-img', st.p.image_proctoring, (v) => { st.p.image_proctoring = v; })),
        h('div', null, h('span', { class: 'lbl' }, 'Enable Safe Assessment Browser', info('Saved with each attempt for the proctoring suite, which applies the safe browser.')),
          yesNo(key + '-sab', st.p.safe_browser, (v) => { st.p.safe_browser = v; }))));
  }
  function linkField(url, label) {
    return h('div', { class: 'invsec' }, h('span', { class: 'lbl' }, label, info(url ? 'Anyone with this link can join. Each person signs in with their own name and email.' : 'Switch on the open link to get one link for everyone.')),
      h('div', { class: 'linkfield' + (url ? '' : ' off') }, h('span', { class: 'lk', html: LINKI }), h('span', { class: 'url', text: url || 'Open link is off' }),
        h('button', { type: 'button', class: 'copyl', disabled: !url, text: 'Copy Link', onclick: (e) => { const b = e.currentTarget; navigator.clipboard.writeText(url).then(() => { b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy Link'; }, 1500); }, () => toast('Copy isn\'t allowed here. Select the link instead.')); } })));
  }
  function editor(htmlStr, placeholders) {
    const ed = h('div', { class: 'rte-body', contenteditable: 'true', role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Invitation template', html: htmlStr });
    const cmd = (c, v) => { ed.focus(); document.execCommand(c, false, v); };
    const btn = (label, title, fn) => h('button', { type: 'button', class: 'rte-b', title, 'aria-label': title, html: label, onmousedown: (e) => e.preventDefault(), onclick: fn });
    const block = h('select', { class: 'rte-s', 'aria-label': 'Text style', onchange: (e) => { cmd('formatBlock', e.target.value); e.target.value = 'p'; } },
      [['p', 'Paragraph'], ['h2', 'Heading'], ['h3', 'Subheading'], ['blockquote', 'Quote']].map(([v, l]) => h('option', { value: v, text: l })));
    const bar = h('div', { class: 'rte-bar' },
      btn('&#8630;', 'Undo', () => cmd('undo')), btn('&#8631;', 'Redo', () => cmd('redo')), h('span', { class: 'rte-sep' }), block, h('span', { class: 'rte-sep' }),
      btn('<b>B</b>', 'Bold', () => cmd('bold')), btn('<i>I</i>', 'Italic', () => cmd('italic')),
      btn(LINKI, 'Link', () => { const u = window.prompt ? window.prompt('Link address (https://…)') : ''; if (u && /^(https?:\/\/|mailto:)/i.test(u)) cmd('createLink', u); else if (u) toast('Use an address starting with https:// or mailto:'); }),
      h('span', { class: 'rte-sep' }),
      btn('&#8226;&#8212;', 'Bulleted list', () => cmd('insertUnorderedList')), btn('1&#8212;', 'Numbered list', () => cmd('insertOrderedList')));
    const chips = h('div', { class: 'rte-chips' }, h('span', { text: 'Insert:' }), placeholders.map((ph) =>
      h('button', { type: 'button', class: 'chip', text: ph, onmousedown: (e) => e.preventDefault(), onclick: () => cmd('insertText', ph) })));
    return { el: h('div', { class: 'rte' }, bar, ed, chips), get: () => ed.innerHTML };
  }
  function renderInvite() {
    const st = S.inv, d = st.data, body = $('invBody'), foot = $('invFoot');
    body.textContent = ''; foot.textContent = ''; body.scrollTop = 0;
    $('invTitle').textContent = 'Send Invitation';
    $('invSub').textContent = d ? d.label + ': ' + d.title : '';
    $('invCredits').innerHTML = d ? COIN + '<span>Credits: <b>' + d.credits + '</b></span>' : '';
    const tabs = $('invTabs'); tabs.textContent = '';
    [['email', 'Email Invitation'], ['link', 'Open Link Invitation']].forEach(([id, label]) => tabs.append(h('button', { type: 'button', role: 'tab', 'aria-selected': String(st.tab === id), text: label, onclick: () => { st.tab = id; st.sent = null; renderInvite(); } })));
    if (!d) { body.append(h('p', { class: 'invsub', text: 'Loading…' })); return; }
    const cancel = h('button', { class: 'obtn ghost', type: 'button', text: 'Cancel', onclick: () => $('invDlg').close() });
    if (st.tab === 'email' && st.sent) {
      const r = st.sent;
      body.append(h('div', { class: 'invsec' }, h('h3', { text: r.email_configured ? 'Invitations sent' : 'Invitations created' }),
        h('p', { class: 'invsub', text: r.email_configured ? 'Each participant got their own link and access code.' : 'Email isn\'t set up on this server yet, so copy each invitation and send it yourself.' }),
        h('div', { class: 'invlist' }, r.results.map((x) => h('div', { class: 'invrow' },
          h('div', null, h('b', { text: x.email }), h('small', { text: x.sent ? 'Sent' : x.error ? x.error : 'Ready to send' })),
          h('span', { class: 'code', text: x.code }), copyBtn(x.text, 'Copy invitation'), copyBtn(x.link, 'Copy link'))))));
      foot.append(h('button', { class: 'obtn ghost', type: 'button', text: 'Invite more', onclick: () => { st.sent = null; renderInvite(); } }), h('button', { class: 'cwt', type: 'button', text: 'Done', onclick: () => $('invDlg').close() }));
      return;
    }
    if (st.tab === 'email') {
      const em = st.email;
      const list = parseEmails(em.text), bad = list.filter((e) => !okEmail(e));
      const hint = h('p', { class: 'hint' + (bad.length || list.length > d.max_emails ? ' bad' : ''), text: bad.length ? 'Check these addresses: ' + bad.join(', ') : list.length > d.max_emails ? 'You can enter up to ' + d.max_emails + ' email addresses at one time (' + list.length + ' entered).' : 'You can enter up to ' + d.max_emails + ' email addresses at one time' });
      const send = h('button', { class: 'cwt', type: 'button', text: d.email_configured ? 'Send Invitation' : 'Create Invitations' });
      const sync = () => { const l = parseEmails(em.text), b = l.filter((e) => !okEmail(e)); send.disabled = st.busy || !l.length || b.length > 0 || l.length > d.max_emails;
        hint.className = 'hint' + (b.length || l.length > d.max_emails ? ' bad' : ''); hint.textContent = b.length ? 'Check these addresses: ' + b.join(', ') : l.length > d.max_emails ? 'You can enter up to ' + d.max_emails + ' email addresses at one time (' + l.length + ' entered).' : 'You can enter up to ' + d.max_emails + ' email addresses at one time'; };
      const ta = h('textarea', { id: 'inv-emails', class: 'inp', rows: '3', placeholder: 'Enter Multiple Email Addresses', value: em.text, oninput: (e) => { em.text = e.target.value; sync(); } });
      const ed = editor(d.template, d.placeholders);
      send.onclick = async () => {
        st.busy = true; send.disabled = true; send.textContent = d.email_configured ? 'Sending…' : 'Creating…';
        try { st.sent = await api('/agents/' + encodeURIComponent(S.agentId) + '/invitations', { method: 'POST', body: { emails: parseEmails(em.text), template_html: ed.get(), image_proctoring: em.p.image_proctoring, safe_browser: em.p.safe_browser } });
          d.template = ed.get(); em.text = ''; toast(st.sent.email_configured ? 'Invitations sent.' : 'Invitations created.'); }
        catch (e) { toast(e.message); }
        st.busy = false; renderInvite();
      };
      body.append(
        h('div', { class: 'invsec' }, h('label', { class: 'lbl', for: 'inv-emails' }, 'Emails', h('span', { class: 'req', text: ' *' }), info('Separate addresses with commas, spaces or new lines. Each person gets their own link and access code.')), ta, hint),
        linkField(d.open_link.enabled ? d.open_link.link : '', d.label + ' link'),
        proctorBlock('em', em),
        h('div', { class: 'invsec' }, h('span', { class: 'lbl' }, 'Invitation Template', info('Placeholders are filled for each participant. Their personal link and access code are always added at the end.')), ed.el,
          !d.email_configured ? h('p', { class: 'hint', text: 'Email isn\'t set up on this server yet. Invitations are created for you to copy and send.' }) : null));
      foot.append(cancel, send); sync();
    } else {
      const ln = st.link;
      const save = h('button', { class: 'cwt', type: 'button', text: 'Save', onclick: async () => {
        save.disabled = true;
        try { const r = await api('/agents/' + encodeURIComponent(S.agentId) + '/open-link', { method: 'POST', body: { enabled: ln.enabled, image_proctoring: ln.p.image_proctoring, safe_browser: ln.p.safe_browser } });
          d.open_link = r; S.row.open_link = r; toast(r.enabled ? 'Open link is on.' : 'Open link is off. It no longer works.'); renderSide(); }
        catch (e) { toast(e.message); }
        renderInvite();
      } });
      body.append(
        h('div', { class: 'invsec' }, h('h3', { text: 'Enable ' + d.label + ' link' }), yesNo('ol-on', ln.enabled, (v) => { ln.enabled = v; }),
          h('p', { class: 'hint', text: 'Note: Enable to allow access, Disable to block it.' })),
        proctorBlock('ol', ln),
        linkField(d.open_link.enabled ? d.open_link.link : '', d.label + ' link'));
      foot.append(cancel, save);
    }
  }
  $('invClose').addEventListener('click', () => $('invDlg').close());

  /* ---------- Help ---------- */
  document.querySelectorAll('[data-help]').forEach((b) => b.addEventListener('click', () => {
    const sup = $('helpSupport');
    const has = !!(OPT && OPT.support_url); if (has) sup.href = OPT.support_url; sup.hidden = !has; sup.parentElement.hidden = !has;
    $('helpDlg').showModal();
  }));
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
      h('div', null, h('span', { class: 'eyebrow', text: opts.compact ? 'TEST RESULT' : 'RESULT' }),
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
    const LISTS = [['did_well', 'What went well'], ['strengths', 'Strengths'], ['improve', 'Areas to improve'], ['try_next', 'Try next time'], ['next_steps', 'Next steps'], ['development_areas', 'Development areas'], ['themes', 'Themes'], ['observations', 'Observations'], ['follow_ups', 'Follow-ups']];
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
  /* Retell's v3 browser client (the v2 RetellWebClient is retired on 2026-10-18),
   * pointed at OUR server. Our /call (or /test-call) has already run every check
   * and created the call with the secret key, so the client's own "create call"
   * is answered with that; whatever else it asks (stop, the live transcript) goes
   * to our relay, with the session cookie as the only credential. It speaks the
   * old event names, so the screens above it don't change. */
  async function retellV3(sdkUrl, base, created) {
    const mod = await import(sdkUrl);
    const handlers = {};
    const fire = (k, a) => { const f = handlers[k]; if (f) f(a); };
    const ourFetch = (url, o) => {
      if (String(url).endsWith('/v3/create-web-call')) {
        return Promise.resolve(new Response(JSON.stringify(created), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      const headers = Object.assign({}, (o && o.headers) || {});
      delete headers.Authorization;
      return fetch(url, Object.assign({}, o, { headers, credentials: 'same-origin' }));
    };
    const client = new mod.RetellClient({ key: 'session', baseURL: base, fetch: ourFetch });
    let call = null;
    return {
      on: (k, f) => { handlers[k] = f; },
      startCall: () => {
        call = client.createWebCall({ agent_id: 'session', transcript: true, hooks: {
          onStatus: (st) => { if (st === 'live') fire('call_started'); },
          onAgentStartTalking: () => fire('agent_start_talking'),
          onAgentStopTalking: () => fire('agent_stop_talking'),
          onTranscript: (tr) => fire('update', { transcript: (tr || []).filter((x) => x && (x.role === 'agent' || x.role === 'user')) }),
          onUpdate: (u) => fire('update', u),
          onEnd: () => fire('call_ended'),
          onError: (e) => fire('error', e)
        } });
        return call.ready;
      },
      mute: () => { if (call) call.mute(); },
      unmute: () => { if (call) call.unmute(); },
      stopCall: () => { if (call) call.end(); }
    };
  }

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
      const client = await retellV3('https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.2/+esm',
        location.origin + '/api/agent-builder/agents/' + encodeURIComponent(S.agentId) + '/retell', r);
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
      await client.startCall();
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
      try { const row = await api('/agents/' + encodeURIComponent(id)); S.brief = row.brief || ''; $('brief').value = S.brief; S.mode = 'roleplay'; renderS1(); loadRow(row); go(3); }
      catch (e) { toast('That agent couldn\'t be opened.'); }
    }
  })();
})();
