/* Contract LocalScan — 前端交互 */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const icon = (name, cls = '') => `<svg class="${cls}"><use href="#i-${name}"></use></svg>`;
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtSize = (n) => n < 1024 ? `${Math.round(n)} B`
    : n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB'
      : (n / 1024).toFixed(0) + ' KB';
  const fmtDate = (ts) => {
    if (!ts) return '—';
    const d = new Date(ts);
    const p = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const levelText = { high: '高风险', medium: '中风险', low: '低风险' };
  const levelColor = { high: '#A4372E', medium: '#B37B2D', low: '#4C6B54' };

  const state = {
    status: null,
    files: [],
    results: [],
    active: 0,
    job: null,
    pollTimer: null,
    history: [],
    kbLaw: '',
    kbCat: '',
    kbInitialized: false,
    tab: localStorage.getItem('cls.tab') || 'risk',
    filter: localStorage.getItem('cls.filter') || 'all',
    config: {},
    consent: null,
    autopilot: null,
    apTimer: null,
    apWatching: false,
    waitTimer: null,
    modelReady: false,
    submitting: false,
    samples: [],
    view: 'workspace',
    submittedFiles: [],
    failedFiles: [],
    custom: { packs: [], stats: null, overrides: {} },
    customFile: null,
    preview: null,
  };

  /* ---------- toast ---------- */
  function toast(msg, type = '') {
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.innerHTML = esc(msg);
    $('#toastWrap').appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .3s, transform .3s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(16px)';
      setTimeout(() => el.remove(), 320);
    }, 3600);
  }

  /* ---------- view switch ---------- */
  function showView(name) {
    state.view = name;
    $$('.view').forEach((v) => v.classList.remove('active'));
    const view = $('#view-' + name);
    if (view) view.classList.add('active');
    $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.nav === name));
    if (name === 'history') renderHistory();
    if (name === 'kb') { initKb(); }
    if (name === 'custom') { renderCustom(); }
    if (name === 'settings') { renderSettings(); refreshGuard(); }
    if (name === 'workspace') renderRecent();
    window.scrollTo(0, 0);
  }

  /* ---------- 使用说明 ---------- */
  function openDoc(key, keepView) {
    if (!keepView) showView('docs');
    $$('.docs-tab').forEach((t) => t.classList.toggle('active', t.dataset.doctab === key));
    $$('.doc-panel').forEach((p) => p.classList.toggle('active', p.id === 'doc-' + key));
  }

  /* ---------- status ---------- */
  async function loadStatus() {
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      state.status = s;
      renderStatus(s);
    } catch (e) {
      toast('本地服务状态读取失败', 'error');
    }
  }

  /* ---------- 顶栏模型胶囊：常态显示在线/离线，下载时显示进度条 ---------- */
  function renderPill() {
    const s = state.status || {};
    const oll = s.ollama || {};
    const enabled = s.use_llm !== false;
    const ready = enabled && oll.online && oll.model_ready && oll.model_supported !== false;
    const ap = state.autopilot || {};
    const pill = $('#modelPill');
    const text = $('#modelPillText');
    const bar = $('#modelPillBar');
    const fill = $('#modelPillFill');
    if (!pill) return;
    const pulling = ap.state === 'starting' || ap.state === 'pulling';
    pill.classList.toggle('pulling', pulling);
    pill.classList.toggle('online', ready && !pulling);
    pill.classList.toggle('offline', !ready && !pulling);
    if (pulling) {
      const pct = Number.isFinite(ap.percent) ? Math.max(1, Math.min(99, Math.round(ap.percent))) : 0;
      text.textContent = `${ap.model || oll.model || '模型'} · 下载中 ${pct}%`;
      bar.hidden = false;
      fill.style.width = Math.max(2, pct) + '%';
      pill.title = `${ap.message || '正在下载本地模型'}（点击查看详情）`;
      return;
    }
    bar.hidden = true;
    pill.title = '本地模型状态，点击进入「系统与隐私 → 本地模型」';
    text.textContent = ap.state === 'error' ? `${oll.model || '本地模型'} · 下载失败`
      : (!enabled ? '规则模式 · 本地模型未启用'
        : (ready ? `${oll.model} · 在线`
          : (oll.online && oll.model_ready ? `${oll.model} · 内存不足`
            : (oll.online ? '模型未就绪' : '规则模式 · 离线'))));
  }

  // 进入页面时先读取一次部署状态：若上次的下载仍在进行，直接接管进度显示
  async function syncAutopilot() {
    try {
      const d = await (await fetch('/api/ollama/autopilot')).json();
      state.autopilot = d;
      renderPill();
      if (d.state === 'starting' || d.state === 'pulling') watchAutopilot();
    } catch (_) { /* 服务尚未就绪时忽略 */ }
  }

  // 下载期间每 1.5 秒刷新一次进度；结束后刷新整体状态并提示一次
  async function watchAutopilot() {
    // 用同步标志防重入：syncAutopilot 与 ensureAutopilot 可能同时拉起轮询
    if (state.apWatching) return;
    state.apWatching = true;
    let wasRunning = false;
    const tick = async () => {
      let d;
      try {
        d = await (await fetch('/api/ollama/autopilot')).json();
      } catch (_) {
        state.apTimer = setTimeout(tick, 3000);
        return;
      }
      state.autopilot = d;
      renderPill();
      const running = d.state === 'starting' || d.state === 'pulling';
      if (running) {
        wasRunning = true;
        state.apTimer = setTimeout(tick, 1500);
        return;
      }
      state.apTimer = null;
      state.apWatching = false;
      if (d.state === 'ready' && wasRunning) {
        await loadStatus();
        await refreshModelCards();
        const on = $('#useLlm')?.checked;
        toast(on ? '本地模型已就绪，后续审阅将使用本地模型'
          : '本地模型已就绪，可在工作台开启模型增强', 'ok');
      } else if (d.state === 'error' && wasRunning) {
        toast('本地模型下载未完成：' + (d.error || '未知原因'), 'error');
      }
    };
    tick();
  }

  function renderStatus(s) {
    const oll = s.ollama || {};
    // 已安装但本机内存达不到推荐档位时，不算真正可用（审阅会自动走规则引擎）
    const ready = oll.online && oll.model_ready && oll.model_supported !== false && s.use_llm !== false;
    const wasReady = state.modelReady === true;
    state.modelReady = ready;
    renderPill();
    // 侧栏显示用户当前选中的模型，而不是固定写死的 Qwen2.5
    const sideModel = $('#sideModel');
    if (sideModel) {
      sideModel.textContent = ready ? oll.model : '规则引擎';
      sideModel.className = ready ? 'ok' : '';
    }
    $('#sideKb').textContent = (s.kb?.total || 0).toLocaleString() + ' 条';
    $('#sideRules').textContent = (s.rules || 0) + ' 条';
    $('#topbarNote').textContent = `规则引擎 × 本地 ${ready ? oll.model : '模型'} × ${(s.kb?.total || 0).toLocaleString()} 条法规`;
    // 按持久化状态同步 checkbox（用户意图），而非模型就绪状态。
    // 模型不可用时会自动降级，但 checkbox 仍反映用户"想用模型"的真实选择。
    $('#useLlm').checked = !!s.use_llm;
    $('#navHistoryCount').textContent = s.history?.total ?? 0;
    $('#sideMeter').style.width = ready ? '100%' : '58%';
    // engine-chip / llmHint / optModel 统一由 syncWorkbenchModelSurface 维护（在本函数末尾，状态完整后调用）
    syncWorkbenchModelSurface();

    // 版本说明里的数量随知识库实际数据渲染，避免法规库更新后文案与实际不符
    const fill = (sel, v) => { const el = $(sel); if (el) el.textContent = v; };
    const juris = s.kb?.jurisdictions || [];
    const cnJuris = juris.find((j) => j.id === 'CN');
    const intlJuris = juris.find((j) => j.id === 'INTL');
    fill('#verLaws', cnJuris ? cnJuris.laws.toLocaleString() : '');
    fill('#verIntl', intlJuris ? String(intlJuris.laws) : '');
    fill('#verTotal', (s.kb?.total || 0).toLocaleString());
    fill('#verRules', String(s.rules || 0));
    // 外联自检：把运行期拦截结果直接呈现在侧栏
    const guard = s.netguard || {};
    if ($('#sideGuard')) {
      const n = guard.attempts || 0;
      setSideGuard(guard.lockdown ? '已锁定：外联全部阻断'
        : (n ? `已拦截 ${n} 次外联尝试` : '未检测到外部访问'), n || guard.lockdown ? 'warn' : 'ok');
      $('#sideGuardBar').title = guard.lockdown
        ? '锁定模式：任何指向本机之外的连接（含域名形式）都会被阻断'
        : (n ? `最近一次拦截目标：${guard.last_target || '未知'}` : '运行期实时监测中，未检测到任何指向本机之外的连接');
    }
    renderGuard(guard);
    fill('#docRules', String(s.rules || 0));
    fill('#docRules2', String(s.rules || 0));
    fill('#docKbTotal', (s.kb?.total || 0).toLocaleString());
  }

  /* ---------- 外联监测 ---------- */
  function setSideGuard(text, tone) {
    const el = $('#sideGuard');
    const bar = $('#sideGuardBar');
    if (el) el.textContent = text;
    if (bar) bar.className = 'guard-bar ' + (tone === 'warn' ? 'warn' : 'ok');
  }

  function renderGuard(g) {
    const list = $('#guardList');
    if (!list) return;
    g = g || {};
    const evs = g.events || [];
    list.innerHTML = evs.length
      ? evs.map((v) => `
        <div class="guard-row">
          <span class="guard-dot"></span>
          <b>${esc(v.target)}</b>
          <em>${esc(v.origin)}</em>
          <span class="guard-time">${esc(v.time)}</span>
        </div>`).join('')
      : '<div class="guard-empty">暂无指向本机之外的连接尝试。监测自应用启动起持续生效。</div>';
    const badge = $('#guardBadge');
    badge.textContent = g.lockdown ? '已锁定' : (g.attempts ? `已拦截 ${g.attempts} 次` : '监测中');
    badge.className = 'badge-soft ' + (g.lockdown || g.attempts ? 'warn' : 'ok');
    const lockBtn = $('#btnGuardLock');
    if (lockBtn) lockBtn.textContent = g.lockdown ? '解除锁定' : '一键断联';
    const note = $('#guardNote');
    if (note) note.textContent = g.lockdown
      ? `锁定中（${g.locked_at || ''}）：域名形式的地址也一并拦截，本机回环不受影响`
      : '默认已拦截全部非本机目标；锁定模式会进一步关掉域名形式的地址';
    if (state.status) state.status.netguard = g;
  }

  async function guardCheck() {
    const btn = $('#btnGuardCheck');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '自检中…';
    try {
      const r = await fetch('/api/netguard/check', { method: 'POST' });
      const d = await r.json();
      renderGuard(d.snapshot);
      const box = $('#guardCheck');
      box.hidden = false;
      box.innerHTML = `<div class="gc-head">自检结果 · ${esc(d.checked_at)}</div>`
        + (d.items || []).map((i) => `
          <div class="gc-row ${esc(i.level)}">
            <span class="gc-dot"></span>
            <b>${esc(i.label)}</b>
            <span>${esc(i.detail)}</span>
          </div>`).join('');
    } catch (e) {
      toast('自检失败，请重试', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  async function refreshGuard() {
    try {
      const r = await fetch('/api/netguard');
      renderGuard(await r.json());
    } catch (e) { /* 服务未就绪时静默 */ }
  }

  async function toggleLockdown() {
    try {
      const cur = !!(state.status?.netguard?.lockdown);
      const r = await fetch('/api/netguard/lockdown', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ on: !cur }),
      });
      const d = await r.json();
      renderGuard(d);
      setSideGuard(d.lockdown ? '已锁定：外联全部阻断'
        : ((d.attempts || 0) ? `已拦截 ${d.attempts} 次外联尝试` : '未检测到外部访问'),
      d.lockdown || d.attempts ? 'warn' : 'ok');
      toast(d.lockdown ? '已锁定：任何外联尝试都会被阻断' : '已解除锁定，恢复默认拦截', 'ok');
    } catch (e) {
      toast('操作失败，请重试', 'error');
    }
  }

  /* ---------- files ---------- */
  function addFiles(list) {
    const incoming = Array.from(list);
    let added = 0;
    for (const f of incoming) {
      if (state.files.length >= 8) { toast('单次最多 8 份合同', 'error'); break; }
      if (!/\.(pdf|docx|txt)$/i.test(f.name)) { toast(`${f.name} 格式不支持`, 'error'); continue; }
      if (f.size > 25 * 1024 * 1024) { toast(`${f.name} 超过 25 MB`, 'error'); continue; }
      if (state.files.some((x) => x.name === f.name && x.size === f.size)) continue;
      state.files.push(f);
      added++;
    }
    if (added) {
      renderFiles();
      toast(`已加入 ${added} 份合同`, 'ok');
    } else if (incoming.length) {
      toast('没有新文件加入（重复或格式不支持）', 'error');
    }
  }

  function renderFiles() {
    const box = $('#fileList');
    if (!state.files.length) {
      box.innerHTML = `<div class="file-empty">文件列表为空，拖入或选择合同开始。</div>`;
    } else {
      box.innerHTML = state.files.map((f, i) => `
        <div class="file-row">
          <div class="file-type">${esc(f.name.split('.').pop())}</div>
          <div style="min-width:0">
            <div class="file-name" title="${esc(f.name)}">${esc(f.name)}</div>
            <div class="file-meta">${fmtSize(f.size)} · 本地读取</div>
          </div>
          <button class="file-remove" data-remove="${i}" title="移除">${icon('x')}</button>
        </div>`).join('');
    }
    $('#fileSummary').textContent = state.files.length
      ? `${state.files.length} 份合同 · 合计 ${fmtSize(state.files.reduce((a, f) => a + f.size, 0))}`
      : '尚未选择合同';
    $('#btnReview').disabled = state.submitting || state.files.length === 0;
  }

  /* ---------- samples ---------- */
  async function loadSamples() {
    try {
      const r = await fetch('/api/samples');
      const data = await r.json();
      state.samples = data.items || [];
      renderSampleMini();
    } catch (e) {}
  }

  function renderSampleMini() {
    const box = $('#sampleMini');
    if (!state.samples.length) { box.innerHTML = '<div class="file-empty">示例文件缺失</div>'; return; }
    box.innerHTML = state.samples.map((s) => `
      <div class="sample-item" data-sample="${esc(s.name)}">
        <div class="file-type">${esc(s.type)}</div>
        <div style="min-width:0"><b>${esc(s.name.replace(/\.\w+$/, ''))}</b><span>${esc(s.description)}</span></div>
        <span class="sample-add">+</span>
      </div>`).join('');
  }

  async function useSamples(names) {
    const files = [];
    for (const name of names) {
      try {
        const r = await fetch('/api/samples/' + encodeURIComponent(name));
        if (!r.ok) throw new Error('下载失败');
        const blob = await r.blob();
        files.push(new File([blob], name, { type: blob.type || 'application/octet-stream' }));
      } catch (e) { toast(`示例 ${name} 载入失败`, 'error'); }
    }
    if (files.length) addFiles(files);
  }

  /* ---------- review ---------- */
  // 提交中的按钮状态集中处理：审阅与重试共用，避免连点重复提交同一批文件
  function setSubmitting(on) {
    state.submitting = on;
    const review = $('#btnReview');
    const retry = $('#btnRetry');
    if (review) {
      review.disabled = on;
      if (!on) review.innerHTML = `开始审阅 ${icon('arrow')}`;
    }
    if (retry) retry.disabled = on;
  }

  async function startReview() {
    if (!state.files.length || state.submitting) return;
    state.submitting = true;
    state.submittedFiles = state.files.slice();
    state.failedFiles = [];
    const fd = new FormData();
    state.files.forEach((f) => fd.append('files', f, f.name));
    fd.append('use_llm', $('#useLlm').checked ? 'true' : 'false');
    setSubmitting(true);
    $('#btnReview').innerHTML = `<span class="spinner"></span> 正在启动本机引擎`;

    try {
      const r = await fetch('/api/review', { method: 'POST', body: fd });
      const data = await r.json();
      if (!r.ok) {
        throw new Error(data.error || '提交失败');
      }
      state.job = { id: data.job_id, progress: state.files.map((f) => ({ filename: f.name, state: 'pending', step: '等待本地处理' })), state: 'queued', results: [] };
      renderProgress();
      showView('progress');
      pollJob(data.job_id);
    } catch (e) {
      toast(e.message || '审阅启动失败', 'error');
      setSubmitting(false);
    }
  }

  function pollJob(jobId) {
    clearTimeout(state.pollTimer);
    const tick = async () => {
      // 任务已被新的提交替换或用户已返回工作台时，丢弃这次响应，避免旧状态覆盖新任务
      if (!state.job || state.job.id !== jobId) return;
      try {
        const r = await fetch('/api/jobs/' + jobId);
        if (!r.ok) throw new Error('任务失效');
        const job = await r.json();
        if (!state.job || state.job.id !== jobId) return;
        state.job = job;
        renderProgress(job);
        if (job.state === 'done' || job.state === 'failed') {
          finishJob(job);
          return;
        }
        state.pollTimer = setTimeout(tick, 900);
      } catch (e) {
        if (!state.job || state.job.id !== jobId) return;
        toast('本地任务中断，请重试', 'error');
        setSubmitting(false);
      }
    };
    tick();
  }

  const STEP_PCT = { '解析文档结构': 18, '抽取关键条款': 52, '运行风险规则引擎': 72, '匹配本地法规知识库': 88, '写入本机审阅记录': 96, '完成': 100 };

  function renderProgress(job) {
    const j = job || state.job;
    if (!j) return;
    const rows = j.progress || [];
    const done = rows.filter((r) => r.state === 'done').length;
    let current = 0;
    const cur = rows.find((r) => r.state === 'processing');
    if (cur) current = (STEP_PCT[cur.step] || 35) / 100;
    const pct = Math.min(100, Math.round(((done + (j.state === 'done' ? 0 : current)) / Math.max(1, rows.length)) * 100));
    $('#overallPct').innerHTML = pct + '<span>%</span>';
    $('#overallBar').style.strokeDashoffset = 326.7 * (1 - pct / 100);
    $('#progressNote').textContent = j.state === 'done'
      ? '本地审阅完成，报告已写入本机数据库。'
      : (cur ? `正在处理「${cur.filename}」：${cur.step}` : '解析合同结构，随后运行规则引擎与法条检索。');
    $('#jobList').innerHTML = rows.map((r) => {
      const stateCls = r.state === 'done' ? 'done' : r.state === 'failed' ? 'failed' : '';
      const p = r.state === 'done' ? 100 : r.state === 'failed' ? 100 : (STEP_PCT[r.step] || (r.state === 'pending' ? 0 : 30));
      const indicator = r.state === 'done' ? icon('tick') : r.state === 'failed' ? icon('alert') : r.state === 'processing' ? '<span class="spinner"></span>' : '';
      return `<div class="job-row">
        <div class="job-row-top">${indicator}<b>${esc(r.filename)}</b>
          <span class="job-state ${stateCls}">${r.state === 'done' ? '完成' : r.state === 'failed' ? '失败' : r.state === 'processing' ? '处理中' : '等待'}</span>
        </div>
        <div class="job-step">${esc(r.step || '')}${r.state === 'done' && r.score !== undefined ? ' · 风险分 ' + r.score : ''}</div>
        <div class="job-progress"><i style="width:${p}%"></i></div>
      </div>`;
    }).join('');
  }

  function finishJob(job) {
    setSubmitting(false);
    // 归集失败的原始 File 对象，供重试使用
    const failedNames = new Set((job.errors || []).map((e) => e.filename));
    state.failedFiles = (state.submittedFiles || []).filter((f) => failedNames.has(f.name));
    state.submittedFiles = [];

    if (!job.results || !job.results.length) {
      // 全部失败：把失败文件放回工作台列表，用户可直接点「开始审阅」重试
      state.files = state.failedFiles.slice();
      state.failedFiles = [];
      renderFiles();
      toast('全部合同处理失败，文件已留在列表，可修正后重试', 'error');
      showView('workspace');
      return;
    }
    state.results = job.results;
    state.active = 0;
    state.tab = 'risk';
    localStorage.setItem('cls.tab', 'risk');
    state.files = [];
    renderFiles();
    renderResult();
    showView('result');
    const ok = job.results.length;
    toast(`${ok} 份合同已完成本地审阅`, 'ok');
    if (job.errors && job.errors.length) {
      toast(`${job.errors.length} 份处理失败：${job.errors[0].error}`, 'error');
    }
    loadStatus();
    loadHistory();
  }

  function retryFailed() {
    if (!state.failedFiles.length) return;
    state.files = state.failedFiles.slice();
    state.failedFiles = [];
    renderFiles();
    startReview();
  }

  /* ---------- result ---------- */
  function current() { return state.results[state.active] || null; }

  /* ---------- 导出（带失败提示） ---------- */
  const EXPORTS = [
    { sel: '#btnPdf', ext: 'pdf', label: 'PDF 报告' },
    { sel: '#btnXlsx', ext: 'xlsx', label: 'Excel 清单' },
    { sel: '#btnDocx', ext: 'docx', label: '批注版合同' },
  ];

  function exportName(ext) {
    const r = current();
    const base = (r && (r.contract_name || r.filename)) || '合同审阅报告';
    return base.replace(/[\\/:*?"<>|]/g, '_').replace(/\.(pdf|docx?|txt)$/i, '') + '.' + ext;
  }

  async function saveExport(el, ext, label, url, filename) {
    url = url || el.getAttribute('href');
    if (!url) return;
    const oldHTML = el.innerHTML;
    el.style.pointerEvents = 'none';
    el.style.opacity = '0.6';
    el.textContent = '生成中…';
    let ok = false;
    let savedPath = '';
    const useDirect = (state.config?.export_mode || 'direct') !== 'browser';
    try {
      const sep = url.includes('?') ? '&' : '?';
      const r = await fetch(url + (useDirect ? sep + 'save=1' : ''));
      if (!r.ok) {
        let msg = `导出失败（HTTP ${r.status}）`;
        try {
          const j = await r.json();
          if (j && j.error) msg = j.error;
        } catch (_) { /* 非 JSON 响应，保留默认文案 */ }
        toast(msg, 'error');
        return;
      }
      const ct = r.headers.get('content-type') || '';
      if (useDirect && ct.includes('application/json')) {
        // 直存模式：后端直接写入 export_dir，返回 JSON 路径
        const j = await r.json();
        if (j && j.ok && j.path) {
          ok = true;
          savedPath = j.path;
          toast(`${label}已保存`, 'ok');
        } else if (j && j.error) {
          toast(j.error, 'error');
        }
      } else {
        // 浏览器下载模式（或直存失败回退）：fetch + blob + <a download>
        const blob = await r.blob();
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objUrl;
        a.download = filename
          ? filename.replace(/[\\/:*?"<>|]/g, '_').replace(/\.(pdf|docx?|txt)$/i, '') + '.' + ext
          : exportName(ext);
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 3000);
        ok = true;
        toast(`${label}已开始下载`, 'ok');
      }
    } catch (e) {
      toast('导出失败，请确认本地服务仍在运行', 'error');
    } finally {
      el.style.pointerEvents = '';
      el.style.opacity = '';
      if (ok) {
        if (savedPath) {
          // 直存成功：显示"在访达中显示"按钮，点击后在系统文件管理器中定位文件
          el.innerHTML = '';
          const span = document.createElement('span');
          span.textContent = '已保存 ✓';
          el.appendChild(span);
          const reveal = document.createElement('button');
          reveal.textContent = '在访达中显示';
          reveal.style.cssText = 'margin-left:8px;font-size:10px;padding:2px 6px;border-radius:4px;border:1px solid #E0D5C0;background:#FBF6EA;color:#7C6A4C;cursor:pointer;';
          reveal.onclick = async (ev) => {
            ev.stopPropagation();
            try {
              await fetch('/api/reveal', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: savedPath })
              });
            } catch (_) { /* 忽略 */ }
          };
          el.appendChild(reveal);
          setTimeout(() => { el.innerHTML = oldHTML; }, 4000);
        } else {
          el.textContent = '已保存 ✓';
          setTimeout(() => { el.innerHTML = oldHTML; }, 1600);
        }
      } else {
        el.innerHTML = oldHTML;
      }
    }
  }

  function bindExports() {
    EXPORTS.forEach(({ sel, ext, label }) => {
      const el = $(sel);
      if (!el) return;
      el.addEventListener('click', (e) => {
        e.preventDefault(); // 不再让 <a> 直接导航，改为能提示失败的 fetch 下载
        saveExport(el, ext, label);
      });
    });
  }

  function renderResult() {
    const r = current();
    if (!r) return;
    state.tab = state.tab || 'risk'; state.filter = state.filter || 'all';
    const resName = r.contract_name || r.filename;
    $('#resTitle').textContent = resName;
    $('#resTitle').title = resName;
    $('#resMeta').textContent = `${r.filename} · ${r.chars.toLocaleString()} 字 · ${r.contract_type} · 审阅于 ${fmtDate(r.created_at)}`;
    $('#btnPdf').href = `/api/reviews/${r.id}/pdf`;
    $('#btnXlsx').href = `/api/reviews/${r.id}/xlsx`;
    $('#btnDocx').href = `/api/reviews/${r.id}/docx`;

    $('#batchTabs').innerHTML = state.results.length > 1 ? state.results.map((x, i) => `
      <button class="batch-tab ${i === state.active ? 'active' : ''}" data-batch="${i}">
        <span>${esc(x.filename)}</span>
        <span class="score-pill ${x.level}">${x.score}</span>
      </button>`).join('') : '';
    $('#batchTabs').style.display = state.results.length > 1 ? 'flex' : 'none';

    // score
    $('#scoreVal').textContent = r.score;
    $('#scoreBar').style.stroke = levelColor[r.level];
    $('#scoreBar').style.strokeDashoffset = 389.6 * (1 - r.score / 100);
    $('#levelLine').innerHTML = `<b style="color:${levelColor[r.level]}">${levelText[r.level]}</b><span>风险指数 · 满分 100，越高越需修订</span>`;

    const c = r.counts || { high: 0, medium: 0, low: 0 };
    const total = c.high + c.medium + c.low;
    const highRatio = total ? Math.round(c.high / total * 100) : 0;
    $('#countChips').innerHTML = `
      <span class="count-chip high"><b>${c.high}</b> 高</span>
      <span class="count-chip medium"><b>${c.medium}</b> 中</span>
      <span class="count-chip low"><b>${c.low}</b> 低</span>
      <span class="count-chip ratio" title="高风险条数占全部命中规则的比例"><b>${highRatio}%</b> 高危占比</span>`;
    $('#verdict').textContent = r.summary;

    const warns = (r.warnings || []).filter((x) => !(r.scan_suspect && /OCR|扫描件|图片/.test(x)));
    const w = warns.length ? warns[0] : '本结论由本地规则与模型生成，供风险预审参考，不替代律师正式意见。';
    $('#warnLine').innerHTML = `${icon('alert')}<span>${esc(w)}</span>`;

    const term = (r.values?.term_start || r.values?.term_end)
      ? `${r.values.term_start || '—'} 至 ${r.values.term_end || '—'}` : '未明确';
    const amtField = (r.fields || []).find((f) => f.key === 'amount_total');
    const amtLabel = amtField ? amtField.label : '合同金额';
    // 模型名跟实际配置走：用户可能用 gemma3 或别的模型，不能把 qwen2.5 写死
    const modelName = (r.engine?.model && r.engine.model !== '规则引擎')
      ? r.engine.model : (state.status?.ollama?.model || '本地模型');
    const srcMap = { llm: `${modelName} 生成`, rule: '规则生成' };
    $('#resMetaCard').innerHTML = [
      ['甲方 / 用人单位', r.party_a || '未识别'],
      ['乙方 / 劳动者', r.party_b || '未识别'],
      [amtLabel, r.amount_total || '未识别'],
      ['合同期限', term],
      ['字段抽取', r.llm_used ? `${modelName} + 规则` : '规则引擎'],
      ['结论来源', srcMap[r.summary_source] || '规则生成'],
      ['法条依据', `${(r.findings || []).reduce((a, f) => a + (f.basis?.length || 0), 0)} 处引用`],
    ].map(([k, v]) => `<div class="meta-item"><span>${k}</span><b title="${esc(v)}">${esc(v)}</b></div>`).join('');

    $('#tabRiskCount').textContent = (r.findings || []).length;
    $('#tabFieldCount').textContent = (r.fields || []).length;
    $('#tabLawCount').textContent = (r.statutes || []).length;
    $('#textChars').textContent = `${r.chars.toLocaleString()} 字 · 仅本机渲染`;

    // 失败重试横幅
    const banner = $('#retryBanner');
    if (banner) {
      if (state.failedFiles.length) {
        $('#retryCount').textContent = state.failedFiles.length;
        banner.hidden = false;
      } else {
        banner.hidden = true;
      }
    }

    // 扫描件 OCR 提示
    const ocrBanner = $('#ocrBanner');
    if (ocrBanner) {
      ocrBanner.hidden = !r.scan_suspect;
      if (r.scan_suspect && r.ocr_used) {
        ocrBanner.classList.add('ocr-ok');
        $('#ocrBannerTitle').textContent = '扫描件 · 已通过本地 OCR 识别';
        $('#ocrBannerDesc').textContent = '识别在本机完成，未上传任何内容。OCR 可能存在错字或漏行，金额、日期、比例等关键信息请对照原件核对。';
      } else if (r.scan_suspect) {
        ocrBanner.classList.remove('ocr-ok');
        $('#ocrBannerTitle').textContent = '疑似扫描件 · OCR 不可用或识别失败';
        $('#ocrBannerDesc').textContent = '仅解析到少量文字，字段抽取与风险结论可能不完整。建议上传文字清晰的电子版合同。';
      }
    }

    renderRisk();
    renderFields();
    renderStatutes();
    renderText();
    bindTabs();
  }

  const sevLabel = (s) => ({ high: '高风险', medium: '中风险', low: '低风险' }[s] || s || '');

  // 来源徽标：自定义规则包的 finding 标注包名，内置不标（默认即内置）
  function srcBadge(source) {
    if (!source || source.type !== 'custom') return '';
    return `<span class="src-badge custom" title="来自自定义规则包">${esc(source.pack_name || '自定义')}</span>`;
  }

  // LLM 复核徽章：显示在 finding 标题行
  function llmBadge(f) {
    if (!f.llm_verified) return '';
    const v = f.llm_verified;
    const titleMap = {
      confirm: '本地 AI 确认此风险成立',
      reject: '本地 AI 认为可能是假阳性',
      doubtful: '本地 AI 拿不准，建议人工复核',
    };
    const clsMap = { confirm: 'confirm', reject: 'reject', doubtful: 'doubtful' };
    const labelMap = { confirm: '🤖 确认', reject: '🤖 排除', doubtful: '🤖 存疑' };
    return `<span class="llm-badge ${clsMap[v] || ''}" title="${esc(titleMap[v] || '')} · 置信度 ${f.llm_confidence ?? '-'}">${labelMap[v] || v}</span>`;
  }

  // LLM 复核详情块：显示在 risk-body 里
  function llmBlock(f) {
    if (!f.llm_verified) return '';
    const v = f.llm_verified;
    const clsMap = { confirm: 'confirm', reject: 'reject', doubtful: 'doubtful' };
    const iconMap = { confirm: '✓', reject: '✗', doubtful: '?' };
    const labelMap = { confirm: 'AI 确认风险成立', reject: 'AI 认为可能是假阳性', doubtful: 'AI 拿不准，建议人工复核' };
    const reasonHtml = f.llm_reason ? `<p class="llm-reason">${esc(f.llm_reason)}</p>` : '';
    const confHtml = f.llm_confidence != null ? `<span class="llm-conf">置信度 ${Math.round(f.llm_confidence * 100)}%</span>` : '';
    return `<div class="llm-verify ${clsMap[v] || ''}">
      <div class="llm-v-head">
        <span class="llm-v-icon">${iconMap[v] || ''}</span>
        <span class="llm-v-label">${labelMap[v] || 'AI 复核'}</span>
        ${confHtml}
      </div>
      ${reasonHtml}
    </div>`;
  }

  // 口径分歧提示卡：同一条款内置与自定义结论不同时双输出，由专业人士判定
  function divergenceCard(f) {
    if (!f.divergence || !Array.isArray(f.divergence_peers) || !f.divergence_peers.length) return '';
    const selfSrc = f.source?.type === 'custom' ? (f.source.pack_name || '自定义包') : '内置规则';
    const peers = f.divergence_peers.map((p) => {
      const isCustom = p.source?.type === 'custom';
      const pSrc = isCustom ? (p.source.pack_name || '自定义包') : '内置规则';
      return `<button class="div-peer" data-peer="${esc(p.id)}" type="button" title="定位到该结论">
        <span class="dp-src ${isCustom ? 'custom' : ''}">${esc(pSrc)}</span>
        <span class="sev-badge ${esc(p.severity)}">${sevLabel(p.severity)}</span>
        <b>${esc(p.title)}</b>${icon('arrow')}
      </button>`;
    }).join('');
    return `<div class="div-card">
      <div class="div-head">${icon('alert')}同一事项存在两套结论（口径分歧）</div>
      <p>本条为「${esc(selfSrc)}」作出的 ${sevLabel(f.severity)} 结论；下方是另一来源对同一条款的并列结论。两套结论均附各自法条出处，本工具不做取舍，请由法务或执业专业人士判定适用口径。</p>
      <div class="div-peers">${peers}</div>
    </div>`;
  }

  function renderRisk() {
    const r = current();
    const all = r.findings || [];
    const c = r.counts || { high: 0, medium: 0, low: 0 };
    const filters = [
      ['all', '全部', all.length],
      ['high', '高风险', c.high],
      ['medium', '中风险', c.medium],
      ['low', '低风险', c.low],
    ];
    $('#riskFilters').innerHTML = filters.map(([k, label, n]) =>
      `<button class="filter-chip ${state.filter === k ? 'active' : ''}" data-filter="${k}">${label} ${n}</button>`).join('');
    const list = state.filter === 'all' ? all : all.filter((f) => f.severity === state.filter);
    if (!list.length) {
      $('#riskList').innerHTML = `<div class="history-empty">该分级下没有命中规则。建议仍由法务复核金额、期限与签署主体。</div>`;
      return;
    }
    $('#riskList').innerHTML = list.map((f, i) => `
      <article class="risk-item ${i === 0 ? 'open' : ''}" data-finding="${f.id}">
        <button class="risk-head">
          <span class="risk-rail ${f.severity}"></span>
          <span class="sev-badge ${f.severity}">${f.severity_label}</span>
          <span class="risk-title">${esc(f.title)}</span>
          <span class="risk-cat">${esc(f.category)}</span>
          ${srcBadge(f.source)}
          ${llmBadge(f)}
          <span class="risk-chev">${icon('arrow')}</span>
        </button>
        <div class="risk-body">
          ${divergenceCard(f)}
          <div class="evidence"><span>条款原文</span>${esc(f.evidence)}</div>
          <div class="risk-cols">
            <div><h5>风险分析</h5><p>${esc(f.analysis)}</p></div>
            <div class="advice"><h5>修改建议</h5><p>${esc(f.advice)}</p></div>
          </div>
          ${llmBlock(f)}
          ${f.revision ? `
          <div class="revision">
            <div class="rev-head"><h5>条款修订建议</h5><button class="copy-btn" data-copy="${esc(f.id)}">复制条款</button></div>
            <p>${esc(f.revision)}</p>
          </div>` : ''}
          <div class="law-refs"><span>法律依据</span>
            ${(f.basis || []).map((b) => `<button class="law-chip" data-article="${esc(b.id)}">《${esc(b.law)}》${esc(b.label)}</button>`).join('')}
          </div>
        </div>
      </article>`).join('');
  }

  async function copyRevision(rid, btn) {
    const f = (current().findings || []).find((x) => x.id === rid);
    if (!f || !f.revision) return;
    try {
      await navigator.clipboard.writeText(f.revision);
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = f.revision;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    const old = btn.textContent;
    btn.textContent = '已复制';
    btn.classList.add('done');
    setTimeout(() => { btn.textContent = old; btn.classList.remove('done'); }, 1600);
  }

  function renderFields() {
    const r = current();
    // 徽标空间有限，只保留冒号前的主名称（qwen2.5:3b → qwen2.5）
    const modelName = ((r.engine?.model && r.engine.model !== '规则引擎')
      ? r.engine.model : (state.status?.ollama?.model || '模型')).split(':')[0];
    const srcMap = { llm: modelName, rule: '规则', none: '未识别' };
    $('#fieldsGrid').innerHTML = (r.fields || []).map((f) => `
      <div class="field-card ${f.source === 'none' ? 'missing' : ''} ${f.source === 'llm' ? 'highlight' : ''}">
        <div class="fc-top">
          <label>${esc(f.label)}</label>
          <span class="source-badge ${f.source}">${srcMap[f.source] || '—'}</span>
        </div>
        <b>${esc(f.value || '未识别')}</b>
        <div class="hint">${esc(f.hint)}</div>
      </div>`).join('');
  }

  function renderStatutes() {
    const r = current();
    const list = r.statutes || [];
    if (!list.length) {
      $('#lawList').innerHTML = '<div class="history-empty">未检索到相关法条。</div>';
      return;
    }
    $('#lawList').innerHTML = list.map((a, i) => `
      <div class="law-card" data-article="${esc(a.id)}">
        <div class="law-card-top">
          <span class="law-name">《${esc(a.law)}》</span>
          <span class="law-label">${esc(a.label)}</span>
          ${a.jurisdiction === 'INTL' ? '<span class="law-tag">国际</span>' : ''}
          ${a.source_pack || a.src === 'custom' ? '<span class="law-tag custom" title="来自自定义规则包">自定义</span>' : ''}
          <span class="law-score">MATCH ${a.score ?? '—'}</span>
        </div>
        <p>${esc(a.text)}</p>
        ${a.law_note ? `<div class="law-note">${esc(a.law_note)}</div>` : ''}
        <div class="law-chapter">${esc(a.jurisdiction === 'INTL' ? '国际规则 · ' + (a.chapter || a.law) : a.chapter || '')}</div>
      </div>`).join('');
  }

  function renderText() {
    const r = current();
    let html = esc(r.text || '');
    const marks = [];
    (r.findings || []).forEach((f) => {
      let ev = (f.evidence || '').replace(/…/g, '').trim();
      if (ev.length < 6 || ev.startsWith('—')) return;
      const core = Array.from(ev.slice(0, 14)).slice(0, 12).join('');
      if (marks.some((m) => m.core === core)) return;
      marks.push({ core, severity: f.severity });
    });
    marks.forEach((m) => {
      const before = html;
      const chars = Array.from(m.core).map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s*');
      try {
        html = html.replace(new RegExp(chars, 'g'), (match) => `<mark class="${m.severity}">${match}</mark>`);
      } catch (e) {}
      // 正则未命中时（含特殊字符或跨行），用 indexOf 兜底定位并标记首次出现
      if (html === before) {
        const idx = html.indexOf(m.core);
        if (idx >= 0) {
          html = html.slice(0, idx) + `<mark class="${m.severity}">${m.core}</mark>` + html.slice(idx + m.core.length);
        }
      }
    });
    $('#contractText').innerHTML = html;
  }

  function bindTabs() {
    const tab = state.tab || 'risk';
    $$('.tab').forEach((t) => {
      const on = t.dataset.tab === tab;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $$('.tab-pane').forEach((p) => p.classList.toggle('active', p.id === 'pane-' + tab));
  }

  /* ---------- history ---------- */
  async function loadHistory() {
    try {
      const r = await fetch('/api/reviews');
      const d = await r.json();
      state.history = d.items || [];
      $('#navHistoryCount').textContent = d.stats?.total ?? state.history.length;
      if (state.view === 'history') renderHistory();
      renderRecent();
    } catch (e) {}
  }

  function renderHistory() {
    const q = ($('#historySearch')?.value || '').trim().toLowerCase();
    const items = state.history.filter((x) => !q || (x.filename + ' ' + (x.contract_type || '')).toLowerCase().includes(q));
    const stats = {
      total: state.history.length,
      high: state.history.filter((x) => x.level === 'high').length,
      medium: state.history.filter((x) => x.level === 'medium').length,
      avg: state.history.length ? Math.round(state.history.reduce((a, x) => a + (x.score || 0), 0) / state.history.length) : 0,
    };
    $('#historyStats').innerHTML = `
      <div class="hist-stat"><b>${stats.total}</b><span>本地审阅记录</span></div>
      <div class="hist-stat"><b style="color:var(--high)">${stats.high}</b><span>高风险合同</span></div>
      <div class="hist-stat"><b style="color:var(--med)">${stats.medium}</b><span>中风险合同</span></div>
      <div class="hist-stat"><b>${stats.avg}</b><span>平均风险分</span></div>`;
    if (!items.length) {
      $('#historyList').innerHTML = `<div class="history-empty">${q ? '没有匹配的合同记录。' : '还没有审阅记录。回到工作台审阅第一份合同。'}</div>`;
      return;
    }
    $('#historyList').innerHTML = `
      <div class="history-row head"><span>合同文件</span><span>风险分</span><span class="hr-hide">分级</span><span class="hr-hide">审阅时间</span><span style="text-align:right">操作</span></div>
      ${items.map((x) => `
        <div class="history-row" data-review="${esc(x.id)}">
          <div class="hr-main">
            <div class="file-type">${esc((x.ext || '').toUpperCase())}</div>
            <div style="min-width:0">
              <b>${esc(x.filename)}</b>
              <span>${esc(x.contract_type || '未分类')} · H${x.high} / M${x.medium} / L${x.low}</span>
            </div>
          </div>
          <div class="hr-cell"><span class="score-pill ${x.level}">${x.score}</span></div>
          <div class="hr-cell hr-hide"><span>${levelText[x.level]}</span></div>
          <div class="hr-cell hr-hide">${fmtDate(x.created_at)}</div>
          <div class="hr-actions">
            <button class="hr-btn" data-open="${esc(x.id)}">查看</button>
            <button class="hr-btn" data-pdf="${esc(x.id)}">PDF</button>
            <button class="hr-btn del" data-delete="${esc(x.id)}">删除</button>
          </div>
        </div>`).join('')}`;
  }

  function renderRecent() {
    const box = $('#recentMini');
    if (!box) return;
    const items = state.history.slice(0, 4);
    if (!items.length) {
      box.innerHTML = '<div class="recent-empty"><svg class="re-icon"><use href="#i-shield"/></svg>还没有记录。<br>载入示例合同，体验一次本地审阅。</div>';
      return;
    }
    box.innerHTML = items.map((x) => `
      <div class="recent-mini-row" data-open="${esc(x.id)}">
        <span class="score-pill ${x.level}">${x.score}</span>
        <div class="rm-main">
          <b title="${esc(x.filename)}">${esc(x.filename)}</b>
          <span>${esc(levelText[x.level])} · ${fmtDate(x.created_at)}</span>
        </div>
      </div>`).join('');
  }

  async function openReview(id, trigger) {
    let oldHtml = '';
    if (trigger) {
      oldHtml = trigger.innerHTML;
      trigger.disabled = true;
      trigger.innerHTML = '<span class="spinner"></span> 读取中';
    }
    try {
      const r = await fetch('/api/reviews/' + id);
      if (!r.ok) throw new Error('读取失败');
      const data = await r.json();
      state.results = [data];
      state.active = 0;
      // 打开历史记录时清掉上一批失败文件，否则结果页会残留无关的「重试」横幅
      state.failedFiles = [];
      state.submittedFiles = [];
      state.tab = 'risk';
      state.filter = 'all';
      localStorage.setItem('cls.tab', 'risk');
      localStorage.setItem('cls.filter', 'all');
      renderResult();
      showView('result');
    } catch (e) {
      toast('无法读取本地记录', 'error');
    } finally {
      if (trigger) {
        trigger.disabled = false;
        trigger.innerHTML = oldHtml;
      }
    }
  }

  async function deleteReview(id, reload = true) {
    if (!confirm('确认删除这条本地审阅记录？导出文件不受影响。')) return;
    const r = await fetch('/api/reviews/' + id, { method: 'DELETE' });
    if (!r.ok) { toast('删除失败，请稍后重试', 'error'); return; }
    toast('记录已从本机删除', 'ok');
    await loadHistory();
    if (reload) showView('history');
  }

  /* ---------- KB ---------- */
  async function initKb() {
    if (!state.status) await loadStatus();
    if (!state.kbInitialized) {
      state.kbInitialized = true;
      renderKbStats();
      renderLawChips();
      renderKbHot();
      await kbSearch('违约责任 合同解除 保密');
    }
  }

  function renderKbStats() {
    const kb = state.status?.kb;
    if (!kb) return;
    const total = kb.total || 0;
    const laws = kb.laws || [];
    const juris = kb.jurisdictions || [];
    const cn = juris.find((j) => j.id === 'CN');
    const intl = juris.find((j) => j.id === 'INTL');
    $('#kbHeroTotal').textContent = total.toLocaleString();
    $('#kbHeroLaws').textContent = cn ? cn.laws : laws.length;
    const cards = [
      [total.toLocaleString(), '总条文'],
      [cn ? cn.count.toLocaleString() : '—', '中国法'],
      [intl ? intl.count.toLocaleString() : '—', '国际规则'],
      [laws.length, '法律 / 规则'],
      ['中英双语', '检索语言'],
    ];
    $('#kbStats').innerHTML = cards.map(([v, k]) => `
      <div class="kb-stat"><b>${v}</b><span>${esc(k)}</span></div>`).join('');
  }

  function renderLawChips() {
    const kb = state.status?.kb || {};
    const laws = kb.laws || [];
    const counts = {};
    laws.filter((l) => (l.jurisdiction || 'CN') === 'CN')
      .forEach((l) => { counts[l.category] = (counts[l.category] || 0) + 1; });
    const cats = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const chip = (value, label, n) => `<button class="${state.kbCat === value && !state.kbLaw ? 'active' : ''}" data-cat="${esc(value)}">${esc(label)}<span class="chip-n">${n}</span></button>`;
    $('#lawChips').innerHTML =
      `<button class="${state.kbCat === '' && !state.kbLaw ? 'active' : ''}" data-cat="">全部</button>` +
      `<span class="group-label">中国法 · 按立法门类</span>` +
      cats.map(([c, n]) => chip(c, c, n)).join('') +
      `<span class="group-label">国际规则</span>` +
      chip('__intl__', '国际规则', laws.filter((l) => (l.jurisdiction || 'CN') === 'INTL').length);
  }

  function renderKbHot() {
    const hot = ['违约金过高', '试用期', '竞业限制 补偿', '个人信息出境', '格式条款免责', '不可抗力', '情势变更 艰难情形', 'force majeure', 'hardship', 'CISG 根本违约'];
    $('#kbHot').innerHTML = '<span>常用检索</span>' + hot.map((h) => `<button data-kb-hot="${esc(h)}">${esc(h)}</button>`).join('');
  }

  async function kbSearch(q) {
    const query = (q ?? $('#kbInput').value).trim();
    if (!query) return;
    $('#kbMs').textContent = '检索中';
    try {
      const url = `/api/kb/search?q=${encodeURIComponent(query)}&cat=${encodeURIComponent(state.kbCat)}&top=14`;
      const r = await fetch(url);
      const d = await r.json();
      $('#kbMs').textContent = d.browse
        ? `${d.count}${d.total > d.count ? '/' + d.total : ''} 条 · 《${d.browse}》全文 · 按条文顺序`
        : `${d.count} 条 · ${d.ms} ms · 本地 BM25`;
      if (!d.items.length) {
        $('#kbResults').innerHTML = '<div class="kb-empty">没有匹配的法条，试试更短的关键词或切换法律。</div>';
        return;
      }
      $('#kbResults').innerHTML = d.items.map((a) => `
        <div class="law-card" data-article="${esc(a.id)}">
          <div class="law-card-top">
            <span class="law-name">《${esc(a.law)}》</span>
            <span class="law-label">${esc(a.label)}</span>
            ${a.jurisdiction === 'INTL' ? '<span class="law-tag">国际</span>' : ''}
            ${a.src === 'custom' || a.source_pack ? '<span class="law-tag custom" title="来自自定义规则包">自定义</span>' : ''}
            ${d.browse ? '' : `<span class="law-score">${a.score}</span>`}
          </div>
          <p>${esc(a.text)}</p>
          ${a.law_note ? `<div class="law-note">${esc(a.law_note)}</div>` : ''}
          <div class="law-chapter">${esc(a.jurisdiction === 'INTL' ? '国际规则 · ' + (a.chapter || a.law) : a.chapter || '')}</div>
        </div>`).join('');
    } catch (e) {
      $('#kbMs').textContent = '检索失败';
    }
  }

  /* ---------- article modal ---------- */
  async function openArticle(id) {
    try {
      const r = await fetch('/api/kb/article/' + encodeURIComponent(id));
      if (!r.ok) throw new Error('not found');
      const a = await r.json();
      $('#modalLaw').textContent = `《${a.law}》 · ${a.law_id.toUpperCase()}`;
      $('#modalLabel').textContent = a.label;
      $('#modalChapter').textContent = a.chapter || '';
      $('#modalText').textContent = a.text;
      $('#btnCopyArticle').dataset.text = `${a.law} ${a.label}：${a.text}`;
      $('#modal').classList.add('open');
    } catch (e) { toast('条文读取失败', 'error'); }
  }

  function closeModal() { $('#modal').classList.remove('open'); }

  /* ---------- 自定义规则 ---------- */
  const CUSTOM_KIND = { regulation: '法规 / 法典', policy: '企业规章', checklist: '审查清单', mixed: '混合' };
  const CUSTOM_JURIS = { ENT: '企业/内部', CN: '国家层面', LOCAL: '地方制度', INTL: '他国/国际' };
  const PARAM_LABELS = {
    penalty_high_pct: '违约金过高（%·合同总价）', penalty_recommend_pct: '违约金建议上限（%）',
    penalty_low_pct_perday: '违约金过低（%·每日）', deposit_max_pct: '定金上限（%）',
    probation_salary_pct: '试用期工资下限（%）', probation_max_months: '试用期最长（月）',
    compete_max_months: '竞业限制上限（月）', lease_max_years: '租赁期限上限（年）',
    lpr_multiplier: '借贷利率上限（倍·LPR）',
  };
  const MODE_LABELS = { match: '出现即违规', absence: '缺失即违规', threshold: '数值口径' };

  async function loadCustom() {
    const r = await fetch('/api/custom/packs');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '读取失败');
    state.custom = { packs: d.packs || [], stats: d.stats || {}, overrides: d.threshold_overrides || {} };
    // 阈值接管口径同步进配置缓存，设置页徽标据此渲染
    if (d.rule_params) state.config.rule_params = d.rule_params;
    state.config.threshold_overrides = d.threshold_overrides || {};
  }

  async function renderCustom() {
    try { await loadCustom(); }
    catch (e) { toast(e.message || '自定义规则读取失败', 'error'); return; }
    const { packs, stats } = state.custom;
    $('#customStats').innerHTML = [
      [packs.length, '已导入规则包'],
      [stats.enabled ?? 0, '启用中'],
      [stats.rules ?? 0, '并列规则'],
      [stats.statutes ?? 0, '入库条文'],
    ].map(([v, k]) => `<div class="kb-stat"><b>${v}</b><span>${esc(k)}</span></div>`).join('');
    const navEm = $('#navCustomCount');
    if (navEm) { navEm.hidden = !packs.length; navEm.textContent = packs.length; }
    if (!packs.length) {
      $('#customList').innerHTML = `<div class="custom-empty">
        <b>尚未导入任何规则包。</b><br>点击右上角「导入规则包」，把企业规章、审查清单、地方制度或他国法典接入三层判断模型。</div>`;
      return;
    }
    $('#customList').innerHTML = packs.map((p) => {
      const thrKeys = Object.keys(p.threshold_overrides || {});
      return `<div class="pack-card ${p.enabled ? '' : 'off'}" data-pack-card="${esc(p.id)}">
        <div class="pack-top">
          <div class="pack-main">
            <b>${esc(p.name)}</b>
            <div class="pack-meta">
              <span class="pack-tag ${p.jurisdiction === 'INTL' ? 'intl' : ''}">${esc(CUSTOM_JURIS[p.jurisdiction] || p.jurisdiction)}</span>
              <span class="pack-tag">${esc(CUSTOM_KIND[p.kind] || p.kind)}</span>
              <span>条文 ${p.statutes} 条</span>
              <span>规则 ${p.rules} 条</span>
              ${thrKeys.length ? `<span class="pack-thr">接管阈值 ${thrKeys.length} 项</span>` : ''}
              <span>${esc(p.source_file || '')} · ${fmtDate(p.imported_at ? p.imported_at * 1000 : 0)}</span>
            </div>
          </div>
          <div class="pack-side">
            <button class="btn text sm" data-pack-detail="${esc(p.id)}">明细</button>
            <label class="switch" title="${p.enabled ? '停用后规则退出评估、阈值回退' : '启用后规则并列生效'}">
              <input type="checkbox" data-pack-toggle="${esc(p.id)}" ${p.enabled ? 'checked' : ''}>
              <span class="track"><i></i></span>
            </label>
            <button class="btn danger sm" data-pack-del="${esc(p.id)}" data-name="${esc(p.name)}">删除</button>
          </div>
        </div>
        <div class="pack-detail" id="pd-${esc(p.id)}"></div>
      </div>`;
    }).join('');
  }

  async function togglePack(pid, enabled) {
    try {
      const r = await fetch(`/api/custom/packs/${encodeURIComponent(pid)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '操作失败');
      if (d.rule_params) state.config.rule_params = d.rule_params;
      state.config.threshold_overrides = d.threshold_overrides || {};
      toast(enabled ? '规则包已启用：条文入库、规则并列生效' : '规则包已停用：规则退出评估，接管阈值已回退', 'ok');
      await loadStatus();
      await renderCustom();
      renderThresholds(state.config?.rule_params);
    } catch (e) {
      toast(e.message, 'error');
      await renderCustom();  // 失败时还原开关位置
    }
  }

  async function deletePack(pid, name) {
    if (!confirm(`确认删除规则包「${name}」？\n\n删除后其条文从知识库移除、规则退出评估、接管的阈值回退。此操作不可恢复。`)) return;
    try {
      const r = await fetch(`/api/custom/packs/${encodeURIComponent(pid)}`, { method: 'DELETE' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '删除失败');
      if (d.rule_params) state.config.rule_params = d.rule_params;
      state.config.threshold_overrides = d.threshold_overrides || {};
      toast('规则包已删除', 'ok');
      await loadStatus();
      await renderCustom();
      renderThresholds(state.config?.rule_params);
    } catch (e) { toast(e.message, 'error'); }
  }

  async function packDetail(pid) {
    const card = document.querySelector(`[data-pack-card="${CSS.escape(pid)}"]`);
    const box = document.getElementById('pd-' + pid);
    if (!card || !box) return;
    if (card.classList.contains('expanded')) { card.classList.remove('expanded'); return; }
    try {
      const r = await fetch(`/api/custom/packs/${encodeURIComponent(pid)}`);
      const p = await r.json();
      if (!r.ok) throw new Error(p.error || '读取失败');
      const rules = p.rules || [];
      const statutes = p.statutes || [];
      const thr = p.threshold_overrides || {};
      const thrRows = Object.entries(thr)
        .map(([k, v]) => `<li>${esc(PARAM_LABELS[k] || k)} → <b>${esc(String(v))}</b></li>`).join('');
      box.innerHTML = `
        <h6>并列规则（${rules.length}）</h6>
        ${rules.length ? `<ul>${rules.map((x) => `<li>[${esc(MODE_LABELS[x.mode] || x.mode)}] ${esc(x.title)}</li>`).join('')}</ul>` : '<div class="pd-empty">无</div>'}
        <h6>接管阈值（${Object.keys(thr).length}）</h6>
        ${thrRows ? `<ul>${thrRows}</ul>` : '<div class="pd-empty">无</div>'}
        <h6>入库条文（${statutes.length}，仅列前 8 条）</h6>
        ${statutes.length ? `<ul>${statutes.slice(0, 8).map((s) => `<li>${esc(s.label)}：${esc(String(s.text).slice(0, 60))}${String(s.text).length > 60 ? '…' : ''}</li>`).join('')}</ul>` : '<div class="pd-empty">无</div>'}`;
      card.classList.add('expanded');
    } catch (e) { toast(e.message, 'error'); }
  }

  /* ---------- 导入与预览 ---------- */
  function openCustomImport() {
    state.customFile = null;
    $('#customFileInput').value = '';
    $('#customFileName').textContent = '未选择';
    $('#customName').value = '';
    $('#customJurisdiction').value = 'ENT';
    $('#customKind').value = 'mixed';
    $('#customLlmRules').checked = false;
    $('#customImportModal').classList.add('open');
  }

  async function submitCustomImport() {
    if (!state.customFile) { toast('请先选择规则包文件', 'error'); return; }
    const btn = $('#btnCustomSubmit');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 本机解析中';
    try {
      const fd = new FormData();
      fd.append('file', state.customFile, state.customFile.name);
      fd.append('name', $('#customName').value.trim());
      fd.append('jurisdiction', $('#customJurisdiction').value);
      fd.append('kind', $('#customKind').value);
      fd.append('llm_rules', $('#customLlmRules').checked ? 'true' : 'false');
      const r = await fetch('/api/custom/packs', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) {
        const detail = (d.errors || []).slice(0, 5).join('\n');
        throw new Error(d.error + (detail ? `\n${detail}` : ''));
      }
      state.preview = d;
      $('#customImportModal').classList.remove('open');
      renderPreview(d);
      $('#customPreviewModal').classList.add('open');
    } catch (e) {
      toast(e.message || '解析失败', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '解析并预览';
    }
  }

  function renderPreview(d) {
    $('#cpName').textContent = d.name;
    $('#cpBadges').innerHTML =
      `<span class="badge-soft">${esc(CUSTOM_KIND[d.kind] || d.kind)}</span>` +
      `<span class="badge-soft">${esc(CUSTOM_JURIS[d.jurisdiction] || d.jurisdiction)}</span>`;
    const errs = d.errors || [];
    $('#cpErrors').hidden = !errs.length;
    $('#cpErrors').innerHTML = errs.map((x) => `<div>· ${esc(x)}</div>`).join('');

    // ① 法条层
    const st = d.statutes || { total: 0, sample: [] };
    $('#cpStatuteCount').textContent = `${st.total} 条`;
    $('#cpStatutes').innerHTML = st.sample.length
      ? st.sample.map((s) => `<div class="cp-statute"><b>${esc(s.label)}</b>${esc(s.text)}</div>`).join('')
        + (st.total > st.sample.length ? `<div class="cp-statute">… 共 ${st.total} 条，此处预览前 ${st.sample.length} 条</div>` : '')
      : '<div class="cp-empty">无条文进入知识库</div>';

    // ② 规则层（默认全选）
    const rules = d.rules || [];
    $('#cpRuleCount').textContent = `${rules.length} 条`;
    $('#cpRules').innerHTML = rules.length ? rules.map((x) => `
      <label class="chk-row cp-rule">
        <input type="checkbox" data-rule-id="${esc(x.id)}" checked>
        <span>
          <b>${esc(x.title)}</b>
          <span class="chk-em">
            <span class="sev-badge ${esc(x.severity)}">${sevLabel(x.severity)}</span>
            <span class="mode-tag ${esc(x.mode)}">${esc(MODE_LABELS[x.mode] || x.mode)}</span>
            ${(x.related || []).map((r) => `<span class="rel-tag">关联 ${esc(r)}</span>`).join('')}
            ${x.llm_derived ? '<span class="llm-tag">模型识别</span>' : ''}
          </span>
          <em>${esc(x.analysis || '')}</em>
        </span>
      </label>`).join('')
      : '<div class="cp-empty">未识别出可执行的检查点；条文仍会进入知识库作为依据</div>';

    // ③ 阈值层（默认全选）
    const thrs = d.thresholds || [];
    $('#cpThrCount').textContent = `${thrs.length} 项`;
    $('#cpThrs').innerHTML = thrs.length ? thrs.map((t) => `
      <label class="cp-thr">
        <input type="checkbox" data-param-key="${esc(t.param_key)}" checked>
        <span class="t-main">
          ${esc(PARAM_LABELS[t.param_key] || t.param_key)}
          <span class="t-change">${esc(String(t.from ?? '默认'))} → ${esc(String(t.to))}</span>
          ${t.replaces ? `<span class="t-rep">将替换当前由「${esc(t.replaces)}」接管的值</span>` : ''}
        </span>
      </label>`).join('')
      : '<div class="cp-empty">未检测到数值口径句，内置阈值保持不变</div>';
  }

  async function confirmCustomImport() {
    const p = state.preview;
    if (!p) return;
    const rules = $$('#cpRules input[data-rule-id]:checked').map((x) => x.dataset.ruleId);
    const params = $$('#cpThrs input[data-param-key]:checked').map((x) => x.dataset.paramKey);
    const btn = $('#btnCustomConfirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 生效中';
    try {
      const r = await fetch(`/api/custom/packs/confirm/${encodeURIComponent(p.preview_id)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rules, params }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '确认失败');
      if (d.rule_params) state.config.rule_params = d.rule_params;
      state.config.threshold_overrides = d.threshold_overrides || {};
      $('#customPreviewModal').classList.remove('open');
      state.preview = null;
      const c = d.counts || {};
      toast(`已导入「${d.pack?.name || p.name}」：条文 ${c.statutes ?? 0} 条入库，规则 ${c.rules ?? 0} 条并列生效${(d.thresholds || []).length ? `，接管阈值 ${d.thresholds.length} 项` : ''}`, 'ok');
      await loadStatus();
      await renderCustom();
      renderThresholds(state.config?.rule_params);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '确认导入并生效';
    }
  }

  /* ---------- settings ---------- */
  async function renderSettings() {
    if (!state.status) await loadStatus();
    try {
      const r = await fetch('/api/config');
      state.config = await r.json();
    } catch (e) {}
    $('#cfgUrl').value = state.config.ollama_url || 'http://127.0.0.1:11434';
    const mode = state.config.export_mode === 'browser' ? 'browser' : 'direct';
    if ($('#cfgExportMode')) $('#cfgExportMode').value = mode;
    if ($('#cfgExportDir')) $('#cfgExportDir').value = state.config.export_dir || '';
    const models = (state.status?.model_options || []).map((m) => m.name);
    const currentModel = state.config.model || state.status?.ollama?.model || '';
    const opts = models.length ? models : [currentModel].filter(Boolean);
    // 内存不足的模型置灰：8 GB 机器选 7b 会把大部分计算压到 CPU，长时间无响应甚至拖垮整机
    const meta = {};
    (state.status?.model_options || []).forEach((m) => { meta[m.name] = m; });
    $('#cfgModel').innerHTML = opts.map((m) => {
      const info = meta[m] || {};
      const blocked = info.supported === false;
      const label = blocked ? `${m}（需 ${info.need_gb} GB 内存）`
        : (info.rec_gb > info.need_gb ? `${m}（推荐 ${info.rec_gb} GB 内存）` : m);
      return `<option value="${esc(m)}" ${m === currentModel ? 'selected' : ''} ${blocked ? 'disabled' : ''}>${esc(label)}</option>`;
    }).join('') || `<option>${esc(currentModel)}</option>`;
    const curInfo = meta[currentModel];
    const memGb = state.status?.sysinfo?.memory_gb;
    if ($('#cfgModelHint')) {
      $('#cfgModelHint').textContent = curInfo && curInfo.supported === false
        ? `当前选择的 ${currentModel} 需要约 ${curInfo.need_gb} GB 运行内存，本机为 ${memGb} GB，审阅时会自动改用规则引擎。`
        : `本机运行内存 ${memGb ? memGb + ' GB' : '未知'}。qwen2.5:3b 适合 8 GB 及以下；gemma3:4b 推荐 16 GB，8 GB 机器可用但每份会慢一些。`;
    }
    renderCatalog();
    renderConsentRow();
    renderModelStatus();
    const dataDir = state.status?.privacy?.data_dir;
    if (dataDir && $('#dataPath')) $('#dataPath').textContent = dataDir;
    $('#factReviews').textContent = state.status?.history?.total ?? 0;
    // 规则数与行业开关：status.rules 含自定义规则，设置页拆分展示
    const customRuleCount = state.status?.custom?.rules || 0;
    const ruleCount = state.status?.rules || 68;
    const builtinCount = Math.max(0, ruleCount - customRuleCount);
    if ($('#rulesBadge')) $('#rulesBadge').textContent = customRuleCount
      ? `内置 ${builtinCount} 条 + 自定义 ${customRuleCount} 条` : `内置 ${builtinCount} 条`;
    if ($('#verRules')) $('#verRules').textContent = ruleCount;
    const ir = state.config?.industry_rules || {};
    document.querySelectorAll('input[data-industry]').forEach((cb) => {
      const key = cb.dataset.industry;
      // 未配置的默认启用
      cb.checked = ir[key] !== false;
    });
    renderThresholds(state.config?.rule_params || {});
  }

  /* ---------- 模型状态行：单独刷新，不重绘整页，避免冲掉用户未保存的阈值输入 ---------- */
  function renderModelStatus() {
    const oll = state.status?.ollama;
    const badge = $('#setModelBadge');
    if (badge) badge.textContent = oll?.online && oll?.model_ready ? '在线' : '离线';
    const box = $('#modelState');
    if (!box) return;
    box.className = 'model-state ' + (oll?.online && oll?.model_ready ? 'online' : '');
    box.innerHTML = `<span class="dot"></span><div>${oll?.online
      ? (oll.model_ready ? `<b>${esc(oll.model)}</b> 已就绪 · ${esc(oll.url)}` : `Ollama 在线，但 <b>${esc(oll.model)}</b> 未安装`)
      : `<b>未连接本地 Ollama</b><br><span style="font-size:10px;color:#A19888">${esc(oll?.error || '请确认 ollama serve 已启动')}</span>`}</div>`;
  }

  // 模型下载完成后只刷新模型状态与推荐模型列表，不动表单里的其它输入
  async function refreshModelCards() {
    try {
      const r = await fetch('/api/config');
      state.config = await r.json();
    } catch (_) {
      return;
    }
    renderModelStatus();
    renderCatalog();
  }

  /* ---------- 推荐模型：一键下载与切换 ---------- */
  function renderCatalog() {
    const box = $('#modelCatalog');
    if (!box) return;
    const items = state.status?.model_catalog || [];
    const cur = state.config?.model || '';
    const memGb = state.status?.sysinfo?.memory_gb;
    box.innerHTML = items.map((m) => {
      const blocked = m.supported === false;
      let action;
      if (blocked) {
        action = `<span class="badge-soft warn">需 ${m.need_gb} GB 内存${memGb ? ` · 本机 ${memGb} GB` : ''}</span>`;
      } else if (!m.installed) {
        action = `<button class="btn ghost sm" data-install="${esc(m.name)}">下载</button>`;
      } else if (m.name === cur) {
        action = '<span class="badge-soft ok">当前使用</span>';
      } else {
        action = `<button class="btn ghost sm" data-use="${esc(m.name)}">设为默认</button>`;
      }
      return `<div class="mc-row${blocked ? ' blocked' : ''}">
        <div class="mc-main">
          <b>${esc(m.title)}</b>
          <span>${esc(m.name)} · ${m.size_gb} GB · 需 ${m.need_gb} GB 内存${m.rec_gb > m.need_gb ? ` · 推荐 ${m.rec_gb} GB` : ''}${m.installed ? ' · 已安装' : ''}</span>
          <em>${esc(m.note)}</em>
          ${blocked ? '<span class="mc-blocked">本机内存不足，暂不可选；换到满足内存的电脑上会自动解锁</span>'
            : (!blocked && memGb && m.rec_gb > m.need_gb && memGb < m.rec_gb
              ? `<span class="mc-blocked">本机 ${memGb} GB 低于推荐的 ${m.rec_gb} GB：可用，但单份审阅会明显变慢</span>` : '')}
        </div>
        <div class="mc-side">${action}</div>
      </div>`;
    }).join('') || '<div class="mc-empty">暂无推荐模型</div>';
    box.innerHTML += '<div class="mc-status" id="catalogStatus"></div>';
  }

  async function useModel(name) {
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: name }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '切换失败');
      toast(`已切换为 ${name}`, 'ok');
      await loadStatus();
      await renderSettings();
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  const RULE_PARAM_DEFAULTS = {
    penalty_high_pct: 30, penalty_recommend_pct: 25, penalty_low_pct_perday: 0.03,
    deposit_max_pct: 20, probation_salary_pct: 80, probation_max_months: 6,
    compete_max_months: 24, lease_max_years: 20, lpr_multiplier: 4,
  };
  const RULE_PARAM_LABEL = {
    penalty_high_pct: ['违约金过高', '%'],
    penalty_recommend_pct: ['违约金建议上限', '%'],
    penalty_low_pct_perday: ['违约金过低', '%/日'],
    deposit_max_pct: ['定金上限', '%'],
    probation_salary_pct: ['试用期工资下限', '%'],
    probation_max_months: ['试用期最长', '月'],
    compete_max_months: ['竞业限制上限', '月'],
    lease_max_years: ['租赁期限上限', '年'],
    lpr_multiplier: ['借贷利率上限', '倍 LPR'],
  };

  function renderThresholds(params) {
    const overrides = state.config?.threshold_overrides || {};
    document.querySelectorAll('input[data-param]').forEach((inp) => {
      const key = inp.dataset.param;
      const val = params?.[key];
      inp.value = val ?? RULE_PARAM_DEFAULTS[key] ?? '';
      // 被自定义包接管的参数：标注来源包名，提示停用可回退
      const item = inp.closest('.thr-item');
      if (!item) return;
      item.querySelector('.thr-src')?.remove();
      const ov = overrides[key];
      item.classList.toggle('taken', !!ov);
      if (ov) {
        const tag = document.createElement('span');
        tag.className = 'thr-src';
        tag.textContent = `由「${ov.pack_name || '自定义包'}」接管`;
        tag.title = '该值由自定义规则包接管；在「自定义规则」页停用对应包即可回退。手工保存阈值会在下次重算时被覆盖。';
        item.querySelector('span')?.appendChild(tag);
      }
    });
    if ($('#thrHint')) $('#thrHint').textContent = '';
    // —— 动态阈值框：已启用自定义包解析出的口径 ——
    const box = $('#thrOverridesList');
    const wrap = $('#thrOverrides');
    if (!box) return;
    const keys = Object.keys(overrides);
    if (!keys.length) {
      box.innerHTML = '<div class="thr-overrides-empty">暂无启用中的自定义口径。导入并启用规则包后，解析出的数值口径会显示在此处。</div>';
      wrap.classList.toggle('show', false);
      return;
    }
    box.innerHTML = keys.map((k) => {
      const ov = overrides[k];
      const [label, unit] = RULE_PARAM_LABEL[k] || [k, ''];
      const cur = (state.config?.rule_params || {})[k] ?? '';
      return `<div class="thr-ov-item">
        <div class="thr-ov-main">
          <b>${esc(label)}</b>
          <span class="thr-ov-val">${ov.value}${esc(unit)}</span>
          <span class="thr-ov-src">来自「${esc(ov.pack_name || '自定义包')}」</span>
        </div>
        <button class="btn text sm ov-promote" data-ov-promote="${esc(k)}" title="将该值写入默认阈值，停用来源包后仍保留">提升为默认</button>
      </div>`;
    }).join('');
    wrap.classList.toggle('show', true);
  }

  async function saveThresholds() {
    const params = {};
    let invalid = 0;
    document.querySelectorAll('input[data-param]').forEach((inp) => {
      const raw = inp.value.trim();
      const v = parseFloat(raw);
      // 空值和非法值不能被静默丢弃，否则用户会以为已经保存成功
      if (!raw || !Number.isFinite(v)) { invalid++; return; }
      params[inp.dataset.param] = v;
    });
    if (invalid) { toast(`有 ${invalid} 项阈值未填写或格式不正确，请检查后再保存`, 'error'); return; }
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_params: params }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '保存失败');
      if (d.config) state.config = d.config;
      renderThresholds(state.config?.rule_params || params);
      $('#thrHint').textContent = '已保存，下次审阅生效';
    } catch (e) {
      toast(e.message || '保存失败', 'error');
    }
  }

  function resetThresholds() {
    renderThresholds(RULE_PARAM_DEFAULTS);
    $('#thrHint').textContent = '已填入默认值，点「保存阈值」后生效';
  }

  async function saveConfig() {
    const payload = {
      ollama_url: $('#cfgUrl').value.trim(),
      model: $('#cfgModel').value,
      export_mode: $('#cfgExportMode')?.value || 'direct'
    };
    const exportDir = $('#cfgExportDir')?.value.trim();
    if (exportDir) payload.export_dir = exportDir;
    // 行业规则开关
    const industry = {};
    document.querySelectorAll('input[data-industry]').forEach((cb) => {
      industry[cb.dataset.industry] = cb.checked;
    });
    if (Object.keys(industry).length) payload.industry_rules = industry;
    try {
      const r = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '保存失败');
      toast('配置已保存到本机', 'ok');
      await loadStatus();
      await renderSettings();
      return true;
    } catch (e) { toast(e.message || '配置保存失败', 'error'); return false; }
  }

  async function testModel() {
    // 保存失败时不要继续用旧状态判断，否则会误报「连接正常」
    if (!(await saveConfig())) return;
    const oll = state.status?.ollama;
    if (oll?.online && oll?.model_ready) toast(`${oll.model} 连接正常`, 'ok');
    else {
      toast(oll?.error || '本地模型未就绪', 'error');
      runDiagnose(); // 失败自动展开自检面板，给出修复路径
    }
  }

  /* ---------- Ollama 自检与一键修复 ---------- */
  let repairPoll = null;

  async function runDiagnose() {
    const panel = $('#repairPanel');
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = '<div class="repair-loading">正在自检 Ollama 环境…</div>';
    try {
      const r = await fetch('/api/ollama/diagnose');
      const d = await r.json();
      if (d.installed && d.running && d.model_ready) {
        panel.hidden = true;
        toast('本地模型环境正常', 'ok');
        return;
      }
      renderRepair(d);
    } catch (e) {
      panel.innerHTML = `<div class="repair-loading">自检失败：${esc(e.message)}</div>`;
    }
  }

  function renderRepair(d) {
    const panel = $('#repairPanel');
    const row = (ok, label, fix) => `
      <div class="repair-row ${ok ? 'ok' : 'bad'}">
        <span class="rr-icon">${ok ? '✓' : '!'}</span>
        <div class="rr-main"><b>${label}</b>${fix || ''}</div>
      </div>`;
    panel.innerHTML = '<div class="repair-head">Ollama 环境自检</div>'
      + row(d.installed, 'Ollama 已安装', d.installed ? '' : '<small>需手动安装：终端执行 <code>brew install ollama</code>，或前往 ollama.com 下载安装包</small>')
      + row(d.running, 'Ollama 服务运行中', (!d.running && d.installed) ? '<small>服务未启动，可在终端执行 <code>ollama serve</code>，或：</small><button class="btn ghost sm" data-repair="start">一键启动服务</button>' : '')
      + row(d.model_ready, `模型 ${esc(d.model)} 已就绪`, (d.running && !d.model_ready) ? `<small>本机未找到该模型，可在终端执行 <code>ollama pull ${esc(d.model)}</code>，或：</small><button class="btn primary sm" data-repair="pull">一键拉取模型</button>` : '')
      + `<div class="repair-status" id="repairStatus">${d.steps.map((s) => `<div>· ${esc(s)}</div>`).join('')}</div>`;
  }

  async function doRepair(action, model) {
    const status = $('#repairStatus') || $('#catalogStatus');
    if (action === 'start') {
      if (status) status.innerHTML = '<div>· 正在启动 Ollama 服务…</div>';
      const r = await fetch('/api/ollama/repair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'start' }) });
      const d = await r.json();
      if (!r.ok) { if (status) status.innerHTML = `<div>· ${esc(d.error || '启动失败')}</div>`; return; }
      setTimeout(() => { loadStatus(); runDiagnose(); }, 2500);
      return;
    }
    if (action === 'pull') {
      const label = model || state.config?.model || '';
      if (status) status.innerHTML = `<div>· 正在下载 ${esc(label)}…</div>`;
      const r = await fetch('/api/ollama/repair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'pull', model }) });
      const d = await r.json();
      if (!r.ok) { if (status) status.innerHTML = `<div>· ${esc(d.error || '下载失败')}</div>`; return; }
      if (repairPoll) clearInterval(repairPoll);
      repairPoll = setInterval(pollRepair, 1200);
    }
  }

  async function pollRepair() {
    try {
      const r = await fetch('/api/ollama/repair/status');
      const d = await r.json();
      const status = $('#repairStatus') || $('#catalogStatus');
      if (!status) { clearInterval(repairPoll); repairPoll = null; return; }
      if (d.state === 'running') {
        const pct = d.percent != null ? Math.round(d.percent) : null;
        const width = pct != null ? Math.max(2, pct) : 8;
        const text = pct != null
          ? `下载中 ${pct}% · ${esc(d.message || '')}`
          : esc(d.message || '正在准备…');
        const stage = d.stage ? ` · ${esc(d.stage)}` : '';
        status.innerHTML = `<div class="repair-progress"><i style="width:${width}%"></i></div><div>· ${text}${stage}</div>`;
      } else if (d.state === 'done') {
        clearInterval(repairPoll); repairPoll = null;
        toast(`${d.model || '模型'} 下载完成`, 'ok');
        await loadStatus();
        await renderSettings();
      } else if (d.state === 'error') {
        clearInterval(repairPoll); repairPoll = null;
        status.innerHTML = `<div>· 下载失败：${esc(d.error || '未知错误')}。可在终端手动执行 ollama pull 重试。</div>`;
      }
    } catch (_) { /* 轮询失败静默重试 */ }
  }

  /* ---------- 本地部署授权：首启弹窗 + 设置页状态 ---------- */
  async function loadConsent() {
    try {
      const r = await fetch('/api/consent');
      state.consent = await r.json();
    } catch (_) {
      state.consent = { deploy_consent: null, consent_at: '' };
    }
    return state.consent;
  }

  async function decideConsent(agree) {
    const fromModal = !$('#consentMask').hidden;
    try {
      const r = await fetch('/api/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agree }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '授权状态保存失败');
      state.consent = d;
    } catch (_) {
      toast('授权状态保存失败，请重试', 'error');
      return false;
    }
    $('#consentMask').hidden = true;
    renderConsentRow();
    // 同意/拒绝部署后立刻进入下一步（激活或指引），不等模型部署完成
    firstRunFlow();
    const toggle = $('#useLlm');
    if (toggle) toggle.checked = agree;
    if (agree) {
      toast('已授权本地部署', 'ok');
      await loadStatus();
      await ensureAutopilot(true);
    } else {
      // 撤回后立刻停掉本地部署相关的轮询与"下载中"显示
      state.autopilot = { state: 'idle', model: state.status?.ollama?.model || '' };
      state.apWatching = false;
      if (state.apTimer) { clearTimeout(state.apTimer); state.apTimer = null; }
      if (state.waitTimer) { clearInterval(state.waitTimer); state.waitTimer = null; }
      renderPill();
      toast(fromModal ? '已选择仅用规则模式，可随时在「系统与隐私 → 本地模型」中开启'
        : '已撤回授权，审阅已切回规则模式', 'ok');
      await loadStatus();
    }
    return true;
  }

  function renderConsentRow() {
    const box = $('#consentRow');
    if (!box) return;
    const c = state.consent;
    if (!c || c.deploy_consent === null) { box.hidden = true; return; }
    // 授权成为初始确认后的不变量：不再提供撤回入口，只显示当前状态
    box.hidden = false;
    box.innerHTML = c.deploy_consent === true
      ? `<span class="cr-text ok">本地部署已授权${c.consent_at ? '（' + esc(c.consent_at) + '）' : ''}</span>`
      : `<span class="cr-text">本地部署未开启，当前仅使用规则引擎</span>`;
  }

  /* ---------- 首启自动部署：静默启动 Ollama 并装齐推荐模型 ---------- */
  // 不再在页面上放部署卡片：部署在后台完成，模型就绪后只提示一次并刷新顶栏状态。
  async function ensureAutopilot(force) {
    if (!force && state.consent?.deploy_consent !== true) return;
    const oll = state.status?.ollama || {};
    if (oll.online && oll.model_ready) {
      // 模型已装好：用户刚点同意时也要给出明确结果，而不是静默返回
      if (force) toast('本地模型已就绪，已启用本地模型', 'ok');
      return;
    }
    try {
      const r = await fetch('/api/ollama/autopilot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (r.status === 403) {
        // 未授权（例如换了新机器或配置被清空）：不自动部署，等用户在设置里确认
        const d = await loadConsent();
        if (d.deploy_consent === null) $('#consentMask').hidden = false;
        return;
      }
      const d = await r.json();
      state.autopilot = d;
      renderPill();
      if (d.state === 'ready') {
        await loadStatus();
        if (force) toast('本地模型已就绪，已启用本地模型', 'ok');
        return;
      }
      if (d.state === 'starting' || d.state === 'pulling') {
        if (force) toast('正在准备本地模型，进度显示在顶栏', 'ok');
        watchAutopilot();
        return;
      }
      if (force) {
        toast(d.state === 'no_ollama' ? '未检测到 Ollama，请先安装后重试'
          : ('本地模型准备未完成：' + (d.error || d.message || '未知原因')), 'error');
      }
    } catch (_) { return; /* 服务刚启动时可能尚未就绪，忽略即可 */ }
    if (state.waitTimer) clearInterval(state.waitTimer);
    // 只在部署失败后补查一段时间，未安装 Ollama 的机器上不能永久每 30 秒轮询一次
    let tries = 0;
    state.waitTimer = setInterval(async () => {
      if (++tries > 10) {
        clearInterval(state.waitTimer);
        state.waitTimer = null;
        return;
      }
      try {
        const s = await (await fetch('/api/status')).json();
        state.status = s;
        renderStatus(s);
        if (s.ollama?.model_ready && s.use_llm !== false) {
          clearInterval(state.waitTimer);
          state.waitTimer = null;
          toast(`${s.ollama.model} 已就绪，可使用模型增强`, 'ok');
        }
      } catch (_) { /* 轮询失败静默重试 */ }
    }, 30000);
  }

  /* ---------- 行业规则开关：改动即自动保存 ---------- */
  async function saveIndustry() {
    const industry = {};
    document.querySelectorAll('input[data-industry]').forEach((cb) => { industry[cb.dataset.industry] = cb.checked; });
    try {
      const r = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ industry_rules: industry }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '保存失败');
      toast('行业规则已保存，下次审阅生效', 'ok');
    } catch (e) { toast(e.message || '行业规则保存失败', 'error'); }
  }

  /* ---------- events ---------- */
  document.addEventListener('click', async (e) => {
    if (e.target.closest('#btnRetry')) { retryFailed(); return; }
    const repairBtn = e.target.closest('[data-repair]');
    if (repairBtn) { doRepair(repairBtn.dataset.repair); return; }
    const installBtn = e.target.closest('[data-install]');
    if (installBtn) { doRepair('pull', installBtn.dataset.install); return; }
    const useBtn = e.target.closest('[data-use]');
    if (useBtn) { useModel(useBtn.dataset.use); return; }
    const copyBtn = e.target.closest('[data-copy]');
    if (copyBtn) { copyRevision(copyBtn.dataset.copy, copyBtn); return; }

    const modelEntry = e.target.closest('#rowModel');
    if (modelEntry) {
      showView('settings');
      setTimeout(() => {
        const card = $('#modelCard');
        if (!card) return;
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.add('flash');
        setTimeout(() => card.classList.remove('flash'), 1200);
      }, 80);
      return;
    }

    const guardEntry = e.target.closest('#rowGuard');
    if (guardEntry) {
      showView('settings');
      setTimeout(() => {
        const card = $('#guardCard');
        if (!card) return;
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.add('flash');
        setTimeout(() => card.classList.remove('flash'), 1200);
      }, 80);
      return;
    }

    const manualBtn = e.target.closest('[data-manual]');
    if (manualBtn) {
      window.open('/api/manual', '_blank');
      return;
    }

    const nav = e.target.closest('[data-nav]');
    if (nav) { e.preventDefault(); showView(nav.dataset.nav); return; }

    const docEntry = e.target.closest('[data-doc]');
    if (docEntry) { openDoc(docEntry.dataset.doc); return; }

    const docTab = e.target.closest('[data-doctab]');
    if (docTab) { openDoc(docTab.dataset.doctab, true); return; }

    const rm = e.target.closest('[data-remove]');
    if (rm) { state.files.splice(Number(rm.dataset.remove), 1); renderFiles(); return; }

    const batch = e.target.closest('[data-batch]');
    if (batch) {
      state.active = Number(batch.dataset.batch);
      renderResult();
      // 换一份合同时回到结果页顶部，避免停留在上一份合同的正文中部
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    const filter = e.target.closest('[data-filter]');
    if (filter) { state.filter = filter.dataset.filter; localStorage.setItem('cls.filter', state.filter); renderRisk(); return; }

    const head = e.target.closest('.risk-head');
    if (head) { head.closest('.risk-item').classList.toggle('open'); return; }

    const art = e.target.closest('[data-article]');
    if (art) { openArticle(art.dataset.article); return; }

    const open = e.target.closest('[data-open]');
    if (open) { openReview(open.dataset.open, open); return; }

    const del = e.target.closest('[data-delete]');
    if (del) { e.stopPropagation(); deleteReview(del.dataset.delete); return; }

    const pdf = e.target.closest('[data-pdf]');
    if (pdf) {
      e.stopPropagation();
      const id = pdf.dataset.pdf;
      const rec = state.history.find((h) => h.id === id);
      const name = rec ? (rec.contract_name || rec.filename) : '合同审阅报告';
      saveExport(pdf, 'pdf', 'PDF 报告', `/api/reviews/${id}/pdf`, name);
      return;
    }

    const sample = e.target.closest('[data-sample]');
    if (sample) { useSamples([sample.dataset.sample]); return; }

    const law = e.target.closest('[data-cat]');
    if (law) { state.kbCat = law.dataset.cat || ''; state.kbLaw = ''; renderLawChips(); kbSearch(); return; }

    const hot = e.target.closest('[data-kb-hot]');
    if (hot) { $('#kbInput').value = hot.dataset.kbHot; kbSearch(hot.dataset.kbHot); return; }

    const tab = e.target.closest('.tab');
    if (tab) {
      state.tab = tab.dataset.tab;
      localStorage.setItem('cls.tab', state.tab);
      bindTabs();
      return;
    }

    if (e.target.closest('#docModal [data-close]')) $('#docModal').classList.remove('open');
    if (e.target.closest('#modal [data-close]')) closeModal();
    if (e.target.closest('#customImportModal [data-close]')) $('#customImportModal').classList.remove('open');
    if (e.target.closest('#customPreviewModal [data-close]')) {
      $('#customPreviewModal').classList.remove('open');
      state.preview = null;
    }

    // 分歧提示卡：定位到另一来源的并列结论
    const peer = e.target.closest('[data-peer]');
    if (peer) {
      let item = document.querySelector(`.risk-item[data-finding="${CSS.escape(peer.dataset.peer)}"]`);
      if (!item) {
        // 对方结论可能被当前分级筛选隐藏，先切回「全部」再找
        state.filter = 'all';
        localStorage.setItem('cls.filter', 'all');
        renderRisk();
        item = document.querySelector(`.risk-item[data-finding="${CSS.escape(peer.dataset.peer)}"]`);
      }
      if (item) {
        item.classList.add('open');
        item.scrollIntoView({ behavior: 'smooth', block: 'center' });
        item.classList.add('flash');
        setTimeout(() => item.classList.remove('flash'), 1200);
      }
      return;
    }

    const pDetail = e.target.closest('[data-pack-detail]');
    if (pDetail) { packDetail(pDetail.dataset.packDetail); return; }

    const pDel = e.target.closest('[data-pack-del]');
    if (pDel) { deletePack(pDel.dataset.packDel, pDel.dataset.name || ''); return; }

    const docBtn = e.target.closest('#btnViewNotices');
    if (docBtn) {
      try {
        const r = await fetch('/api/notices').then((x) => x.json());
        $('#docModalTitle').textContent = '第三方组件与数据来源声明';
        $('#docModalText').textContent = r.content || '文件缺失';
        $('#docModal').classList.add('open');
      } catch {
        toast('加载失败，请重试', 'error');
      }
    }
  });

  $('#btnBrowse').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  $('#btnSamples').addEventListener('click', async (e) => {
    const names = state.samples.map((s) => s.name);
    if (!names.length) return toast('示例加载中，请稍后', 'error');
    const btn = e.currentTarget;
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 载入中';
    try { await useSamples(names); }
    finally { btn.disabled = false; btn.innerHTML = oldHtml; }
  });

  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));
  dz.addEventListener('keydown', (e) => {
    if (e.target !== dz) return;
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#fileInput').click(); }
  });

  // 结果页 tab 支持方向键 / Home / End 切换
  $('#tabbar').addEventListener('keydown', (e) => {
    const tabs = $$('.tab');
    const i = tabs.findIndex((t) => t.classList.contains('active'));
    if (i < 0) return;
    let next = -1;
    if (e.key === 'ArrowRight') next = (i + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  });

  $('#btnReview').addEventListener('click', startReview);

  $('#historySearch').addEventListener('input', renderHistory);
  $('#btnClearHistory').addEventListener('click', async () => {
    if (!confirm('确认清空全部本地审阅记录？此操作不可恢复。')) return;
    const r = await fetch('/api/reviews/clear', { method: 'POST' });
    if (!r.ok) { toast('清空失败，请稍后重试', 'error'); return; }
    toast('本地记录已清空', 'ok');
    await loadHistory();
    showView('history');
  });
  $('#btnClearAll').addEventListener('click', async () => {
    if (!confirm('确认清空全部本地记录？报告与合同文本缓存都会删除。')) return;
    const r = await fetch('/api/reviews/clear', { method: 'POST' });
    if (!r.ok) { toast('清空失败，请稍后重试', 'error'); return; }
    toast('全部本地记录已清空', 'ok');
    await loadStatus();
    await loadHistory();
  });

  // 一键卸载与清理
  $('#btnUninstall')?.addEventListener('click', async () => {
    const tip = [
      '【警告】此操作不可撤销，以下内容将被永久删除：',
      '· 全部审阅历史与合同文本缓存',
      '· 应用设置与配置',
      '· 导出目录中的报告、清单与批注版合同',
      '· 应用本体 ContractLocalScan.app',
      '',
      '删除后无法通过任何方式恢复。如需保留报告，请先备份。',
      '',
      '请输入「卸载」二字确认执行：',
    ].join('\n');
    const ok = window.prompt(tip, '');
    if (ok !== '卸载') return;
    const btn = $('#btnUninstall');
    btn.disabled = true;
    btn.textContent = '清理中…';
    try {
      const r = await fetch('/api/uninstall', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: '卸载' }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) throw new Error(data.error || '清理失败');
      // 展示已删除路径
      const list = $('#uninstallDoneList');
      list.innerHTML = '';
      (data.deleted || []).forEach(p => {
        const li = document.createElement('li');
        li.textContent = p;
        list.appendChild(li);
      });
      (data.kept || []).forEach(p => {
        const li = document.createElement('li');
        li.textContent = p + '（非本工具专用目录，已保留）';
        list.appendChild(li);
      });
      $('#uninstallDone').hidden = false;
    } catch (e) {
      toast('卸载清理失败：' + e.message, 'error');
      btn.disabled = false;
      btn.textContent = '一键卸载与清理';
    }
  });

  $('#btnGuardCheck').addEventListener('click', guardCheck);
  $('#btnGuardLock').addEventListener('click', toggleLockdown);

  $('#kbSearchBtn').addEventListener('click', () => kbSearch());
  $('#kbInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') kbSearch(); });

  $('#btnSaveConfig').addEventListener('click', saveConfig);
  $('#btnThresholdSave').addEventListener('click', saveThresholds);
  $('#btnThresholdReset').addEventListener('click', resetThresholds);
  // 自定义口径提升为默认：把当前 override 值写入 rule_params，即使停用来源包也保留
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ov-promote]');
    if (!b) return;
    const key = b.dataset.ovPromote;
    const ov = state.config?.threshold_overrides?.[key];
    if (!ov) { toast('该口径已失效，请刷新页面', 'error'); return; }
    const cur = (state.config?.rule_params || {});
    const params = { ...cur, [key]: ov.value };
    fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rule_params: params }),
    }).then(async (r) => {
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '保存失败');
      if (d.config) state.config = d.config;
      renderThresholds(state.config?.rule_params);
      toast('已提升为默认阈值，停用来源包后仍保留', 'ok');
    }).catch((err) => toast(err.message || '提升失败', 'error'));
  });
  $('#btnTestModel').addEventListener('click', testModel);
  $('#btnRepair')?.addEventListener('click', runDiagnose);
  document.querySelectorAll('input[data-industry]').forEach((cb) => cb.addEventListener('change', saveIndustry));
  $('#btnConsentAllow')?.addEventListener('click', () => decideConsent(true));
  $('#btnConsentDeny')?.addEventListener('click', () => decideConsent(false));
  $('#consentRow')?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-consent]');
    if (b) decideConsent(b.dataset.consent === 'on');
  });

  // 工作台开关：唯一控制"用不用本地模型"的持久开关。
  // 关掉 → 顶栏 pill 显示"规则模式 · 未启用"，engine-chip 降级为"规则模式"，llmHint 提示走规则引擎
  $('#useLlm')?.addEventListener('change', async () => {
    const on = $('#useLlm').checked;
    try {
      const r = await fetch('/api/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_llm: on }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '保存失败');
      if (d.config) state.config = d.config;
      await loadStatus();
      syncWorkbenchModelSurface();
      toast(on ? '已启用本地模型，后续审阅将优先使用模型抽取'
        : '已切回规则模式，后续审阅不加载模型（每份约 1 秒）', 'ok');
    } catch (e) {
      toast(e.message || '保存失败', 'error');
      $('#useLlm').checked = !on;  // 保存失败时还原 UI
    }
  });

  // engine-chip 点击 → 跳转到「系统与隐私 → 本地模型」
  $('#engineChip')?.addEventListener('click', () => navigate('settings'));

  // 三表面同步：顶栏 pill（renderPill 已响应 status.use_llm）、engine-chip、llmHint
  function syncWorkbenchModelSurface() {
    const s = state.status || {};
    const enabled = !!s.use_llm;
    const oll = s.ollama || {};
    const ready = oll.online && oll.model_ready && oll.model_supported !== false;
    const chip = $('#engineChip');
    const label = chip?.querySelector('.engine-chip-label');
    const modelEl = $('#optModel');
    const hint = $('#llmHint');
    if (!chip) return;
    if (!enabled) {
      chip.classList.add('off');
      chip.classList.remove('on');
      if (label) label.textContent = '当前';
      if (modelEl) modelEl.textContent = '规则模式';
      if (hint) hint.textContent = '当前使用规则引擎，切换上方开关可启用本地模型增强抽取（每份约 10–30 秒）。';
      return;
    }
    chip.classList.remove('off');
    chip.classList.add('on');
    if (label) label.textContent = '当前模型';
    if (modelEl) modelEl.textContent = oll.model || '检测中';
    if (hint) hint.textContent = ready ? `调用本机 ${oll.model || '本地模型'} 抽取关键字段，约 10–30 秒 / 份；推理过程完全在本机完成。`
      : (oll.online ? `本地模型尚未就绪，审阅将自动降级为规则引擎抽取。`
        : `本地模型未运行，审阅将自动降级为规则引擎抽取。可在「系统与隐私」启动。`);
  }

  $('#btnAbout').addEventListener('click', () => { showView('settings'); toast('全部处理均在本机完成，无遥测、无云端账号'); });

  $('#btnCopyArticle').addEventListener('click', async (e) => {
    const text = e.currentTarget.dataset.text || '';
    try { await navigator.clipboard.writeText(text); toast('条文已复制', 'ok'); }
    catch (err) { toast('复制失败，请手动选择', 'error'); }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeModal();
      $('#docModal').classList.remove('open');
      $('#customImportModal').classList.remove('open');
      if ($('#customPreviewModal').classList.contains('open')) {
        $('#customPreviewModal').classList.remove('open');
        state.preview = null;
      }
    }
  });

  /* ---------- 自定义规则页事件 ---------- */
  $('#btnCustomImport')?.addEventListener('click', openCustomImport);
  $('#btnCustomFilePick')?.addEventListener('click', () => $('#customFileInput').click());
  $('#customFileInput')?.addEventListener('change', (e) => {
    state.customFile = e.target.files?.[0] || null;
    $('#customFileName').textContent = state.customFile
      ? `${state.customFile.name} · ${fmtSize(state.customFile.size)}` : '未选择';
  });
  $('#btnCustomSubmit')?.addEventListener('click', submitCustomImport);
  $('#btnCustomConfirm')?.addEventListener('click', confirmCustomImport);
  // 启停开关在动态渲染的包卡片上，走 change 委托
  document.addEventListener('change', (e) => {
    const t = e.target.closest('input[data-pack-toggle]');
    if (t) togglePack(t.dataset.packToggle, t.checked);
  });

  /* ---------- 首启：新手指引 ---------- */
  function firstRunFlow() {
    if (window.ClsTour && window.ClsTour.shouldAutoStart()) {
      setTimeout(() => window.ClsTour.start(), 400);
    }
  }


  /* ---------- init ---------- */
  (async function init() {
    renderFiles();
    bindExports();
    await Promise.all([loadStatus(), loadSamples(), loadHistory(), loadConsent()]);
    // 本地部署会读取本机内存并下载模型：首次启动必须先获得用户授权
    if (state.consent?.deploy_consent === null) {
      $('#consentMask').hidden = false;
    } else {
      // 页面可能在上一轮下载中途被关掉：先接手正在跑的部署进度，再按需补齐
      syncAutopilot();
      ensureAutopilot();
      firstRunFlow();
    }
    // 访问地址：显示本机服务实际监听的地址。若 8765 被占用而自动切换了端口，
    // 首次进入时提示一次，避免用户按文档里的 8765 手动访问时打不开。
    const hostEl = $('#sideHost');
    if (hostEl) hostEl.textContent = location.host;
    if (location.port && location.port !== '8765' && !sessionStorage.getItem('cls_port_notice')) {
      sessionStorage.setItem('cls_port_notice', '1');
      setTimeout(() => {
        toast(`8765 端口已被其他程序占用，本次已改用 ${location.host}。请以此地址为准，不要手动改回 8765。`, 'ok');
      }, 1200);
    }
    // 加载配置（含导出方式 export_mode），供导出按钮判断走直存还是浏览器下载
    try {
      const cr = await fetch('/api/config');
      state.config = await cr.json();
    } catch (_) { /* 配置加载失败时使用默认值 direct */ }
    renderRecent();
  })();
})();
