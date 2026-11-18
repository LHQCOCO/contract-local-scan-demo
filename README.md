# Contract LocalScan · 本地合同风险审阅

[![tests](https://github.com/LHQCOCO/contract-local-scan-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/LHQCOCO/contract-local-scan-demo/actions/workflows/ci.yml)

把一份合同拖进浏览器，本机离线跑完，出来一份能逐条核对的审阅报告：命中哪些风险条款、原文是哪一句、依据哪条法律、建议怎么改。可以导出成 PDF、Word、Excel。

![审阅结果页：左侧本地运行状态，右侧风险清单，每条附条款原文、风险分析、修改建议与法条依据](docs/screenshot-review.png)

*这是在 macOS 上跑本仓库代码、审 `samples/软件开发服务合同.txt` 得到的结果：18 条命中，2 高 / 8 中 / 8 低。每条都带条款原文和修改建议，后面的法条可以点开。左栏是运行状态，法库 63,653 条、规则 191 条、监听地址 127.0.0.1。*

**给谁用**：送律师之前想先做一遍初筛的法务、业务和创业者。也适合想看看确定性规则、本地 BM25 检索、运行时断网这套东西怎么落地的人。

**三分钟验证真假**

1. 看图。下面三张图都是本仓库代码在本机跑出来的界面和导出件，不是设计稿。
2. 跑测试。`pip install -r requirements.txt && python -m pytest tests -q`，结果应该是 `385 passed`。CI 在 Ubuntu 和 macOS 上跑同一套用例。
3. 读代码。规则在 `cls/risks.py`，BM25 在 `cls/kb.py`（k1=1.45, b=0.72），断网在 `cls/netguard.py`，导出在 `cls/reporting.py`。

**运行环境**：Python 3.11 或 3.12。`python app.py` 之后打开 <http://127.0.0.1:8765>，法规库索引几秒内建好。

## 你会看到什么

| 导出件 | 离线可验证 |
| --- | --- |
| ![导出的 PDF 报告](docs/screenshot-export-pdf.png) | ![外联监测面板](docs/screenshot-offline.png) |
| 本机生成的 8 页 PDF：审阅结论、关键字段抽取（每格都标了来源）、按等级排的风险清单，后面附法条原文。同一份结果还能导成带 16 条 Word 原生批注的 DOCX，以及 Excel 清单。 | 「系统与隐私 → 外联监测」里能看到运行期被拦下的每一次非回环连接，也能一键断联。整份报告和知识库都在 127.0.0.1 内完成。 |

---

## 1. 它解决什么问题

合同审阅有三个绕不开的约束：条款得看全，依据得能查，内容不能外传。

通用大模型能审合同，但结论不稳定，引的法条经常是编的，而且把合同原文传上云本身就是合规风险。律所和法务 SaaS 做得好一些，按份收费，日常「这份采购合同有没有坑」这种用法划不来。真正高频的是初筛：送律师之前，先把明显不利的条款、缺的必备条款、超阈值的数字挑出来，附上法条。

这个工具只做这一层。本地进程加本机浏览器页面，不联网也能跑。每条结论都带原文证据和法条 id，可以点开核对。结果能导出，也能存进历史。

## 2. 架构与处理流程

```
浏览器（templates/index.html + static/app.js）
        │  上传 PDF / DOCX / TXT（≤25MB，单次多份）
        ▼
Flask 应用 app.py ──► 后台线程逐份处理（前端轮询 /api/jobs/<id> 拿进度）
        │
        ├─ cls/parsing.py      解析：PDF 文本层 → 无文本层则本机 OCR（rapidocr）；DOCX → 段落；TXT
        ├─ cls/extraction.py   抽取：当事人、金额、期限、付款、违约、争议解决、合同类型（关键词候选 + 可选模型消歧）
        ├─ cls/risks.py        规则引擎：191 条规则，match / absence / threshold / check 四种模式，打分分级
        ├─ cls/kb.py           法条检索：自实现 BM25 召回 → 按合同类型路由法域 → 去重后附到每条 finding
        ├─ cls/llm.py          （可选）本机 Ollama：字段抽取补全、条款复核，全部走 127.0.0.1
        └─ cls/reporting.py    导出：PDF（证据句定位到原文段落并加批注）、DOCX（真批注）、XLSX
        │
        ▼
cls/store.py（SQLite 历史记录） + data/ 本地目录
```

运行期还有一层 `cls/netguard.py`：它装了 socket 钩子，除 127.0.0.1 和 localhost 之外的连接一律拦掉并记录。页面上的外联面板能看到每次被拦的尝试。

### 规则引擎

- 191 条规则，25 个类别：劳动、采购、租赁、借款、建设工程、股权、担保、保密、数据与个人信息、刑事风险、跨境合规等。分高、中、低三档。
- 四种判定模式：
  - `match`：正则命中不利表述，比如「甲方逾期付款不承担任何违约责任」。
  - `absence`：判必备条款缺失，阈值是 marker 命中率 40%。全用 `all()` 太严会漏检，全用 `any()` 会把写得全的合同判成缺。
  - `threshold`：可参数化的阈值（逾期付款天数、违约金比例、试用期工资比例等），值存在用户配置里。
  - `check`：需要看上下文的复合检查，劳动、隐私、数据、行业专项走这一类。
- 评分：`score = min(98, 5 + Σ权重)`，高 16 / 中 8 / 低 3；≥55 高风险，≥28 中风险。
- 可以导入自定义规则包（JSON）。和内置规则在同一条款上冲突时标 `divergence`，两套结论并排列出来，谁也不覆盖谁。

### 知识库

1,480 部法规 / 63,653 条条文（含 CISG、纽约公约、UNCITRAL 示范法、UNIDROIT 通则），条文全文随仓库分发。

## 3. 关键技术选择

### 3.1 为什么用确定性规则打底，而不是让模型直接出结论

1. 结论要能拿去和对方谈。「第 6.1 条约定日 30% 违约金，超过《民法典》第 585 条可调整范围」这种话，得句句能定位到原文。正则命中的证据句本身就是原文切片，模型生成的解释不是。
2. 规则是数据，能写测试，能统计误报。191 条规则配 385 个用例，改一条规则马上知道影响了哪类合同。换成 prompt，同样的改动只能靠抽样目测。
3. 失败的时候能说清楚为什么。模型给不出结论，一般就返回「不确定」；规则引擎能告诉你，是合同里根本没有争议解决条款，还是有条款但没约定管辖地。
4. 规则模式零依赖、毫秒级，结果逐字节可复现，适合当默认路径。模型只在值得花 10 秒的地方介入：字段抽取补全、多候选合同类型消歧、高风险条款复核。模型失败就自动退回纯规则的结果。

### 3.2 为什么自实现 BM25，而不引入向量库

1. 法条检索靠的是术语匹配。「违约金过高」要的是《民法典》第 585 条，靠「违约金」这个词和法条标签就能找到。向量检索在这里又贵又不准，还容易召回语义相近但适用条件不同的条文。
2. 打分公式是 IDF 加权词频饱和度（k1=1.45, b=0.72），命中哪几个词、覆盖了多少查询词、为什么这条排在前面，全都能算出来、打出来。换成 embedding，排序就变成黑盒，规则调试会立刻失去抓手。
3. 零依赖，零模型文件。jieba 分词加纯 Python 倒排索引，启动就能用。不用下载几百 MB 的模型，也不用担心不同机器上向量模型版本不一致导致结果漂移。
4. 工程化的检索策略比换检索器更管用。现在真正解决问题的是这些：查询侧用 `jieba.cut` 粗粒度分词，避免「互联网/互联/联网」重复计分；用 IDF 覆盖率过滤掉只命中停用词的伪召回；按合同类型路由法域，劳动争议只查劳动法域；单法典结果数封顶，防止《民法典》霸屏。

### 3.3 其他取舍

- 历史记录用 SQLite。要按时间倒序分页、按 id 删除、做统计，拿 JSON 文件硬撑迟早得自己写一个小型数据库。
- 后台线程加轮询就够了。审阅是分钟级以内的短任务，轮询实现简单，断线之后页面自己恢复，也省掉长连接的状态管理。
- 本地模型只走 127.0.0.1。`/api/config` 对 Ollama 地址做 host 白名单校验，客户端没法把请求引向外部地址。

## 4. 如何运行

需要 Python 3.11 或 3.12（CI 在 Ubuntu 与 macOS 上覆盖这两个版本）。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py           # 或 ./run.sh
```

打开 <http://127.0.0.1:8765>。首次启动会加载法规库并建索引（约数秒）。

如果本机装了 [Ollama](https://ollama.com) 也拉了模型，可以在设置页打开 AI 辅助。不装的话，规则能力一样是全的。

示例合同在 `samples/`（服务合同 TXT、采购框架合同 PDF、劳动合同 DOCX），也可以直接上传自己的文件。

## 5. 评测方法

```bash
python -m pytest tests -q          # 385 个用例
```

测试按能力分层：

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_*_rules.py`（7 个） | 每类合同的规则回归：触发、不触发、证据句、法条依据是否存在 |
| `tests/test_kb.py` | BM25 检索、法域路由、标签检索、法条计数与知识库完整性 |
| `tests/test_extraction_amount.py` | 金额、日期、主体抽取 |
| `tests/test_custom.py` | 自定义规则导入、阈值规则、坏正则隔离 |
| `tests/test_review_route.py` / `test_docx_route.py` | 上传 → 审阅 → 结果接口的端到端路径 |
| `tests/test_reporting_*.py` | PDF / DOCX / XLSX 导出结构 |
| `tests/test_hardening.py` | 路径穿越、非法文件名、超大文件等安全边界 |
| `tests/test_ocr.py` | 无文本层 PDF 的 OCR 兜底 |
| `tests/test_model_memory_gate.py` | 本机内存不足时拒绝加载大模型 |

CI 在 Ubuntu 与 macOS、Python 3.11/3.12 上跑同一套用例，并额外做一次知识库冒烟（法条总量 + BM25 有返回）。

想手工验证一份合同：上传 `samples/软件开发服务合同.txt`，应至少命中「无预付款 + 验收后 180 日付款」「逾期付款不担责」「日 30% 违约金」「甲方单方任意解除」四类问题，并给出对应法条。

## 6. 已知限制

- 这是初筛，不能当法律意见。规则偏向召回「明确不利」和「明确缺失」的条款，需要结合交易背景判断的（比如商业合理性）它给不了结论。
- 规则是枚举式的。191 条规则覆盖 25 类常见合同，长尾合同（如涉外工程、衍生品）覆盖有限，缺的类别只能新增规则。
- `absence` 模式有取舍。marker 命中率 40% 这个阈值，遇到只截取了部分章节的合同会偏保守，宁可漏报也不误报。
- 条款修订建议为占位文本。`cls/revisions.py` 只保留了接口与索引结构，逐条修订草案还没写，UI 上显示的是模板文案。
- OCR 只处理 PDF。图片型 DOCX 不支持；扫描件识别质量取决于清晰度。
- 单机单用户。没有账号体系，`127.0.0.1` 绑定加本机文件存储，不支持多人并发协作。
- 展示版的数据裁剪。`data/sources/laws/`（抓取缓存）与 `data/sources/repo/`（LawRefBook 快照）未入库，可以用 `scripts/fetch_statutes.py` 重建；完整法库以 4 个分片形式随仓库分发。

## 7. 目录结构

```
app.py                  Flask 应用：路由、任务队列、审阅流水线编排
cls/
  parsing.py            PDF / DOCX / TXT 解析与 OCR 兜底
  extraction.py         关键字段抽取、合同类型判定
  risks.py              规则引擎（191 条内置规则 + 自定义规则）
  revisions.py          条款修订草案索引（当前为占位结构）
  kb.py                 BM25 检索、法域路由、查询扩展
  llm.py                本机 Ollama 可选能力
  netguard.py           运行期外联拦截
  reporting.py          PDF / DOCX / XLSX 导出与原文定位
  store.py              SQLite 历史记录
  custom.py             自定义规则包导入与校验
  config.py             路径、配置读写
templates/ static/      前端页面
data/statutes*.json     法规库（statutes_extra_1..4.json 为完整法库分片）
data/sources/           构建法规库用的原文（民法典、刑法等）与国际条约 PDF
samples/                示例合同
tests/                  385 个用例
scripts/
  build_kb.py           从 data/sources/*.txt 构建核心法库
  fetch_statutes.py     抓取扩充法库（生成 37MB 中间文件）
  split_statutes.py     把中间文件切成 <15MB 分片
  build_intl.py         CISG / 纽约公约 / 示范法
  build_upicc.py        UNIDROIT 通则
  sync_public.py        从私有源重建本仓库（见下）
  showcase/             展示版补丁与 CI 配置
```

## 8. 维护：如何更新这个展示仓

展示仓不手工维护副本。`scripts/sync_public.py` 会把私有源重新剥离、切片、打补丁、自检，再同步过来：

```bash
python scripts/sync_public.py --dry-run          # 只构建 + 自检，不落盘
python scripts/sync_public.py                    # 同步到工作区，人工 review 后自行提交
python scripts/sync_public.py --commit "发布 v1.1.3 展示版" --push
```

剥离规则（`STRIP_DIRS` / `STRIP_FILES`）和代码补丁（`scripts/showcase/patches/`）都在仓库里，能审计也能改。私有源码如果改到了补丁涉及的位置，脚本会直接失败并提示重建补丁，不会悄悄漏掉带授权校验的代码。

## 9. 关于本展示仓

- 代码和法规数据来自完整产品 Contract LocalScan 1.1.2，剥离了授权校验（`cls/license.py`）、渠道与打包配置、密钥占位和运行期数据。规则引擎、检索、解析、导出和 385 条测试都是生产代码。
- 截图取自 macOS 上运行本仓库代码的界面和导出件，示例合同为虚构主体。
- 本仓库只用于展示，不提供安装包与商业授权。

