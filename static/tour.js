/* Contract LocalScan · 分步使用指引
   原生实现，零依赖：遮罩聚光 + 气泡卡片，自动跨视图切换。 */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const LS_KEY = 'cls_tour_done_v2';

  const STEPS = [
    {
      center: true,
      title: '欢迎使用 Contract LocalScan',
      body: '这是一份约 2 分钟的分步指引，会带你走遍工具的每个关键区域——包括<b>自定义规则</b>，以及「系统与隐私」里各项设置的作用和影响。中途想停下时，点卡片上的「取消」或按 <b>ESC</b> 即可；点击指引外的其他区域<b>不会</b>中断指引。'
    },
    {
      view: 'workspace', target: '.upload-panel', pos: 'right',
      title: '上传区 · 一切从这里开始',
      body: '拖入或选择合同文件，支持 <b>PDF / DOCX / TXT</b>，单次最多 <b>8 份</b>、单份 25 MB。文件只进入本机内存与本地数据库，<b>不会发起任何外网请求</b>。手头没有合同时，可载入三份示例体验。'
    },
    {
      view: 'workspace', target: '.llm-toggle-row', up: '.llm-toggle-row', pos: 'right',
      title: '模型增强开关 · 精度与速度的取舍',
      body: '<b>开启（默认）</b>：调用本机 Ollama 模型抽取甲乙方、金额、期限等关键字段，更智能，每份约 10–30 秒。<br><b>关闭</b>：纯规则引擎抽取，秒级完成，适合批量快筛。<br>模型离线时系统自动降级为规则模式，不会因此中断审阅。'
    },
    {
      view: 'workspace', target: '#btnReview', pos: 'top',
      title: '开始审阅 · 启动流水线',
      body: '点击后逐份执行「结构解析 → 条款抽取 → 风险研判 → 本地存证」。完成后记录自动进入右栏「最近审阅」与「历史记录」页，报告可随时重新导出。'
    },
    {
      view: 'workspace', target: '.recent-mini-panel', pos: 'left',
      title: '最近审阅 · 一键回看',
      body: '最近 4 次审阅记录。左侧<b>分数药丸</b>用颜色标示风险等级（红 = 高 / 黄 = 中 / 绿 = 低），点击任意一条即可打开完整报告。「全部」跳转历史记录页。'
    },
    {
      view: 'history', target: '#view-history .page-head', pos: 'bottom',
      title: '历史记录 · 你的本地档案库',
      body: '全部审阅存档都保存在本机 SQLite。支持按名称筛选、回看、导出 PDF / Excel / 批注版合同、删除单条。「清空记录」会删除全部历史——<b>不可恢复</b>，执行前请确认重要报告已导出。'
    },
    {
      view: 'kb', target: '.kb-search', pos: 'bottom',
      title: '法规知识库 · 独立检索工具',
      body: '内置 <b>6 万余条</b>法规条文（中国规范文本 + 国际规则）。输入条款关键词、法条编号或业务场景（如「竞业限制 补偿」）即可查原文。审阅报告引用的每条法条都出自这里，全程本地检索，<b>无在线请求</b>。'
    },
    {
      view: 'custom', target: '#view-custom .page-head', pos: 'bottom',
      title: '自定义规则 · 把你的规范装进来',
      body: '面向保密或特殊合规场景：点「导入规则包」，把企业规章、内部审查清单、地方制度甚至他国法典导入。<b>法条进知识库</b>、<b>规范句派生为检查规则</b>、<b>数值口径接管阈值</b>，停用即回退。适合长期只审某一类合同的用户。'
    },
    {
      view: 'docs', target: '#docsTabs', pos: 'bottom',
      title: '使用说明 · 四篇文档随时查',
      body: '<b>问题与方法</b>：能解决什么、怎么判断、边界在哪；<b>规则引擎与本地模型</b>：规则分组、法定阈值与分工；<b>使用与导出</b>：审阅四阶段、格式限制、三种导出与模型连不上时的逐项排查；<b>卸载与清理</b>：换机或停用后如何彻底移除应用、数据与模型。<br>工作台右上角的说明列表也能直接跳到对应章节。'
    },
    {
      view: 'settings', target: '#cfgUrl', up: 'label', pos: 'top',
      title: '本地模型设置 · 为什么只能填本机地址',
      body: '<b>Ollama 地址被硬性限制为 127.0.0.1 / localhost</b>——这是「零上传」的技术兜底：即使误填外部地址也会被拒绝，合同文本永远发不出本机。<br>模型选择：3b 模型 8 GB 内存即可流畅运行；16 GB 以上可切换 7b 获得更高抽取精度。'
    },
    {
      view: 'settings', target: '#btnTestModel', up: '.field-actions', pos: 'top',
      title: '测试连接 · 失败后的自我修复',
      body: '改动配置后点<b>测试连接</b>验证模型可用。若测试失败，可点旁边的<b>自检与修复</b>：工具会逐层诊断「Ollama 是否安装 → 服务是否运行 → 模型是否拉取」，并给出可执行的一键修复。工具完全离线、没有云端后台，所有排障都由它自己完成。'
    },
    {
      view: 'settings', target: '#cfgExportMode', up: 'label', pos: 'top',
      title: '导出方式 · 两种模式怎么选',
      body: '<b>直接保存（推荐）</b>：报告静默写入下方指定目录，不弹任何对话框，适合频繁导出。<br><b>浏览器下载</b>：每次弹出「存储为」对话框，自己控制每份报告的存放位置。<br>两种模式生成的文件内容完全相同，仅保存位置的决策方式不同。'
    },
    {
      view: 'settings', target: '#industryToggle', pos: 'top',
      title: '行业规则开关 · 让结果更聚焦',
      body: '默认八个行业全部启用（技术 / 采购 / 运输 / 房地产 / 股权合伙 / 担保金融 / 消费生活 / 经营服务）。如果你只审某一类合同（如 HR 只审劳动合同），可关掉无关行业规则，<b>避免无关条款刷屏</b>。通用规则（主体、价款、违约、期限等）始终启用，不受开关影响。<b>改动即时自动保存</b>，下次审阅生效。'
    },
    {
      view: 'settings', target: '#thresholdToggle', pos: 'top',
      title: '规则阈值 · 数值红线的兜底',
      body: '展开后可修改 <b>违约金上限、定金比例、竞业期限</b> 等数值口径。<br>这些默认值本身就是法定红线（如违约金 30%、定金 20%、竞业 24 个月），<b>一般不要改动</b>。确有内部标准（例如公司要求违约金上限 28%）时才在这里调，保存后下次审阅生效，无需重启。<br>若导入了自定义规则包，其中「不得超过 X%」这类数值口径句会接管对应阈值，来源会在此标注，停用规则包即回退原值。'
    },
    {
      view: 'settings', target: '#rowGuard', pos: 'right',
      title: '外部访问 · 实时外联监测',
      body: '这一行是运行期外联监测的入口，下方状态条显示当前结果：<b>未检测到外部访问</b>，或「已拦截 N 次」。<br>点击它打开「外联监测」卡片，可看到每次被拦截的目标、调用来源与时间，并有两个按钮：<b>立即自检</b>逐项核对拦截器、模型地址与锁定状态；<b>一键断联</b>进入锁定模式，连域名形式的地址也一并拦截，本机模型与回环连接不受影响。<br>什么时候点：怀疑合同被外传、在公共网络下使用工具，或想彻底锁死外网时。'
    },
    {
      view: 'settings', target: '#view-settings .danger-zone', pos: 'top',
      title: '清空全部本地记录 · 不可恢复',
      body: '<span class="warn">不可恢复。</span>一键删除本机 SQLite 中的全部审阅历史与缓存。适用场景：定期清理敏感合同、转让电脑前。执行前请确认重要报告已导出。<br>它只清审阅记录，保留配置。'
    },
    {
      view: 'docs', tab: '[data-doctab="uninstall"]', target: '.uninstall-zone', pos: 'top',
      title: '一键卸载与清理 · 换机或彻底停用',
      body: '<span class="warn">不可恢复。</span>自动删除应用数据目录、导出目录，并在退出后移除应用本体。审阅记录、报告与配置<b>将全部清除</b>。<br>适用场景：换新电脑、转让旧机、彻底停用。只想清理审阅历史、保留配置时，请用上一步的「清空全部本地记录」。'
    },
    {
      center: true,
      view: 'workspace',
      title: '指引完成',
      body: '你已经了解工具的每一处关键功能。之后随时可点击顶部「使用指引」重温。<br>现在，拖入第一份合同，或从右侧「示例卷宗」载入示例，开始你的第一次审阅。'
    }
  ];

  let idx = 0, active = false;
  let mask = null, spot = null, tip = null;

  function build() {
    mask = document.createElement('div'); mask.className = 'tour-mask';
    spot = document.createElement('div'); spot.className = 'tour-spot';
    tip = document.createElement('div'); tip.className = 'tour-tip';
    document.body.append(mask, spot, tip);
    // 不再因点击遮罩而退出：遮罩只负责拦截误触，退出必须显式点「取消」或按 ESC
    tip.addEventListener('click', onTipClick);
    document.addEventListener('keydown', onKey);
  }

  function start() {
    if (active) return;
    active = true; idx = 0;
    build();
    render();
  }

  function exit() {
    if (!active) return;
    active = false;
    [mask, spot, tip].forEach((e) => e && e.remove());
    mask = spot = tip = null;
    document.removeEventListener('keydown', onKey);
    try { localStorage.setItem(LS_KEY, '1'); } catch (_) { /* ignore */ }
  }

  function next() {
    if (idx < STEPS.length - 1) { idx += 1; render(); } else { exit(); }
  }
  function prev() {
    if (idx > 0) { idx -= 1; render(); }
  }

  function onKey(e) {
    if (!active) return;
    if (e.key === 'Escape') exit();
    else if (e.key === 'ArrowRight' || e.key === 'Enter') next();
    else if (e.key === 'ArrowLeft') prev();
  }

  function onTipClick(e) {
    const b = e.target.closest('[data-tour]');
    if (!b) return;
    const a = b.dataset.tour;
    if (a === 'next') next();
    else if (a === 'prev') prev();
    else if (a === 'cancel') exit();
  }

  function gotoView(view) {
    if (!view) return;
    const nav = document.querySelector(`.nav-item[data-nav="${view}"]`);
    if (nav) nav.click();
  }

  function resolveTarget(s) {
    if (!s.target) return null;
    let el = $(s.target);
    if (el && s.up) el = el.closest(s.up);
    return el;
  }

  function render() {
    const s = STEPS[idx];
    gotoView(s.view);
    tip.innerHTML = `
      <div class="tour-count">${idx + 1} / ${STEPS.length}</div>
      <h4>${s.title}</h4>
      <div class="tour-body">${s.body}</div>
      <div class="tour-actions">
        <div class="tour-nav">
          ${idx > 0 ? '<button class="tour-btn ghost" data-tour="prev">上一步</button>' : ''}
          <button class="tour-btn ghost" data-tour="cancel">取消</button>
          <button class="tour-btn primary" data-tour="next">${idx === STEPS.length - 1 ? '完成' : '下一步'}</button>
        </div>
      </div>`;
    if (s.tab) {
      // 目标位于某个折叠/分页区域内时，先切到对应标签再定位
      const tab = document.querySelector(s.tab);
      if (tab) tab.click();
    }
    // 视图切换、配置回填都会引起二次布局，多次校正避免高亮框落在旧位置
    const settle = () => { if (active) position(s); };
    requestAnimationFrame(settle);
    setTimeout(settle, 180);
    setTimeout(settle, 480);
  }

  function position(s) {
    if (!active) return;
    const el = resolveTarget(s);
    // 元素不存在或处于隐藏视图（尺寸为 0）时，退化为居中卡片，避免高亮框乱飘
    const box = el ? el.getBoundingClientRect() : null;
    if (!el || (!box.width && !box.height)) {
      spot.style.display = 'none';
      mask.style.background = 'rgba(13,18,24,.55)';
      tip.classList.add('center');
      tip.style.left = '50%'; tip.style.top = '50%';
      tip.style.transform = 'translate(-50%,-50%)';
      return;
    }
    tip.classList.remove('center');
    tip.style.transform = '';
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    const r = el.getBoundingClientRect();
    const pad = 7;
    spot.style.display = 'block';
    spot.style.left = (r.left - pad) + 'px';
    spot.style.top = (r.top - pad) + 'px';
    spot.style.width = (r.width + pad * 2) + 'px';
    spot.style.height = (r.height + pad * 2) + 'px';
    mask.style.background = 'transparent'; // 聚光的 box-shadow 即为遮罩

    const m = 12;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const place = (side) => {
      if (side === 'bottom') return { left: r.left + r.width / 2 - tw / 2, top: r.bottom + pad + m };
      if (side === 'top') return { left: r.left + r.width / 2 - tw / 2, top: r.top - pad - m - th };
      if (side === 'left') return { left: r.left - pad - m - tw, top: r.top + r.height / 2 - th / 2 };
      return { left: r.right + pad + m, top: r.top + r.height / 2 - th / 2 };
    };
    const pref = s.pos || 'bottom';
    let p = place(pref);
    if (pref === 'bottom' && p.top + th > window.innerHeight - m) p = place('top');
    else if (pref === 'top' && p.top < m) p = place('bottom');
    else if (pref === 'right' && p.left + tw > window.innerWidth - m) p = place('left');
    else if (pref === 'left' && p.left < m) p = place('right');
    p.left = Math.max(m, Math.min(p.left, window.innerWidth - tw - m));
    p.top = Math.max(m, Math.min(p.top, window.innerHeight - th - m));
    tip.style.left = p.left + 'px';
    tip.style.top = p.top + 'px';
  }

  window.ClsTour = { start, shouldAutoStart };

  // 首次进入是否自动展示：只读取状态，何时展示由 app.js 的 firstRunFlow 决定
  function shouldAutoStart() {
    try { return !localStorage.getItem(LS_KEY); } catch (_) { return false; }
  }

  function hook() {
    const b1 = $('#btnTour');
    if (b1) b1.addEventListener('click', start);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hook);
  else hook();
})();
