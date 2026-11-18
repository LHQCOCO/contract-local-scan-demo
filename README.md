# Contract LocalScan

[![tests](https://github.com/LHQCOCO/contract-local-scan-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/LHQCOCO/contract-local-scan-demo/actions/workflows/ci.yml)

Contract LocalScan 是一个本地运行的合同初审工具。在浏览器里拖入或选择一份 PDF、Word 或 TXT 合同，页面会列出命中的风险条款、对应的原文和法条，结果可以导出成 PDF、Word 或 Excel。解析、检索和导出都在本机完成，不联网也能跑。

![审阅结果页](docs/screenshot-review.png)

*这是审 `samples/软件开发服务合同.txt` 的结果：18 条命中，2 高 / 8 中 / 8 低。*

## 功能

- 工具内置 191 条规则，覆盖劳动、采购、租赁、借款、建设工程、股权、担保、保密、数据与个人信息、刑事风险、跨境合规等 25 个类别，按高、中、低三档打分。
- 法条检索用的是自实现的 BM25；法库包含 1,480 部法规、63,653 条条文，随仓库一起分发。
- 工具支持 PDF、DOCX、TXT 三种格式；没有文本层的 PDF 会先走本机 OCR。单份文件不超过 25 MB，一次最多上传 8 份。
- 结果可以导出成三种文件：8 页 PDF 审阅报告、带 16 条 Word 原生批注的 DOCX，以及 16 个字段的 Excel 清单。
- 工具在运行时会拦截所有对外连接：除 127.0.0.1 和 localhost 之外的连接一律记录并拦掉，页面上还可以一键断联。
- 装上 Ollama 后，你可以用本机模型补全字段、复核条款，请求只发往 127.0.0.1。不装模型也能使用全部规则能力。

## 快速开始

### 环境要求

运行需要 Python 3.11 或 3.12。CI 在 Ubuntu 上跑测试套件，覆盖 Python 3.11 和 3.12。

### 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 运行

```bash
python app.py           # 或 ./run.sh
```

在浏览器里打开 <http://127.0.0.1:8765>。首次启动会加载法规库并建索引，约需数秒。

`samples/` 里放了三份示例合同：服务合同 TXT、采购框架合同 PDF、劳动合同 DOCX。你也可以直接上传自己的文件。

### 测试

```bash
python -m pytest tests -q          # 385 个用例
```

CI 在 Ubuntu 上跑测试套件，覆盖 Python 3.11 和 3.12，并额外做一次知识库冒烟（法条总量 + BM25 有返回）。

## 用法

打开页面后，把合同拖进虚线框，或者点「选择文件」。页面支持 PDF、DOCX、TXT 三种格式，单份文件不超过 25 MB，一次最多上传 8 份。处理完成后会进入结果页：左栏是本地运行状态，右栏是风险清单，每条带条款原文、风险说明、修改建议和法条，法条可以点开看全文。结果页可以直接导出 PDF、Word 和 Excel，导出过的结果也能从历史记录里重新生成。

![导出的 8 页 PDF 报告](docs/screenshot-export-pdf.png)

*导出的 PDF 依次包含审阅结论、关键字段及来源、风险清单和法条原文。*

![外联监测面板](docs/screenshot-offline.png)

*外联监测面板会列出运行期拦下的非回环连接，需要时也可以一键断联。*

如果想手工验证，可以上传 `samples/软件开发服务合同.txt`，应该至少命中「无预付款 + 验收后 180 日付款」「逾期付款不担责」「日 30% 违约金」「甲方单方任意解除」四类问题，并给出对应法条。

## 工作原理

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

运行期还有一层 `cls/netguard.py`：它装了 socket 钩子，除 127.0.0.1 和 localhost 之外的连接一律拦掉并记录，页面上的外联面板能看到每次被拦的尝试。

### 规则引擎

- 规则引擎内置 191 条规则，覆盖 25 个类别，按高、中、低三档打分。
- 规则有四种判定模式：
  - `match` 用正则匹配不利表述，比如「甲方逾期付款不承担任何违约责任」。
  - `absence` 判必备条款是否缺失，阈值是 marker 命中率 40%。全用 `all()` 太严会漏检，全用 `any()` 会把写得全的合同判成缺。
  - `threshold` 用可参数化的阈值（逾期付款天数、违约金比例、试用期工资比例等），阈值存在用户配置里。
  - `check` 做需要看上下文的复合检查，劳动、隐私、数据、行业专项走这一类。
- 评分公式是 `score = min(98, 5 + Σ权重)`，高 16 / 中 8 / 低 3；≥55 高风险，≥28 中风险。
- 工具支持导入自定义规则包（JSON）。如果自定义规则和内置规则在同一条款上冲突，工具会标 `divergence`，两套结论并排列出来，谁也不覆盖谁。

### 法条检索

检索是自己写的 BM25，不引入向量库。打分公式是 IDF 加权词频饱和度（k1=1.45, b=0.72），命中哪几个词、覆盖了多少查询词、为什么这条排在前面，都能算出来、打出来。查询侧用 `jieba.cut` 粗粒度分词，避免「互联网/互联/联网」重复计分；用 IDF 覆盖率过滤只命中停用词的伪召回；按合同类型路由法域，劳动争议只查劳动法域；单法典结果数封顶，防止《民法典》霸屏。

法条检索靠术语匹配。「违约金过高」要的是《民法典》第 585 条，靠「违约金」这个词和法条标签就能找到。向量检索在这里又贵又不准，还容易召回语义相近但适用条件不同的条文。

### 外联拦截

`/api/config` 对 Ollama 地址做 host 白名单校验，客户端没法把请求引向外部地址。除此之外，`cls/netguard.py` 在进程里装着 socket 钩子，任何非回环连接在建立前就被拦下并记进日志。

### 设计取舍

工具默认用规则审阅，模型只做补充。每条结论都要能拿去和对方谈，正则命中的证据句本身就是原文切片；模型生成的解释不能当作原文证据。规则以数据形式存放，可以写测试、统计误报，191 条规则配 385 个用例，改一条马上知道影响了哪类合同。模型只在值得花 10 秒的地方介入：字段抽取补全、多候选合同类型消歧、高风险条款复核，失败就自动退回纯规则结果。规则模式本身零依赖、毫秒级，结果逐字节可复现。

历史记录存在 SQLite 里。如果要按时间倒序分页、按 id 删除、做统计，拿 JSON 文件硬撑迟早得自己写一个小型数据库。后台线程加轮询就够了，审阅是分钟级以内的短任务，用轮询省掉了长连接的状态管理。

## 项目结构

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

测试按能力分成九组：规则回归、BM25 检索与法域路由、字段抽取、自定义规则、上传与结果接口、三种导出格式、安全边界、OCR 兜底、内存门限。

## 已知限制

- 这是初筛，不能当法律意见。规则偏向召回「明确不利」和「明确缺失」的条款，需要结合交易背景判断的（比如商业合理性）它给不了结论。
- 规则是枚举式的。191 条规则覆盖 25 类常见合同，长尾合同（如涉外工程、衍生品）覆盖有限，缺的类别只能新增规则。
- `absence` 模式有取舍。marker 命中率 40% 这个阈值，遇到只截取了部分章节的合同会偏保守，宁可漏报也不误报。
- 条款修订建议为占位文本。`cls/revisions.py` 只保留了接口与索引结构，逐条修订草案还没写，UI 上显示的是模板文案。
- OCR 只处理 PDF，图片型 DOCX 不在支持范围内；扫描件识别质量取决于清晰度。
- 应用是单机单用户的。它没有账号体系，服务只绑定 127.0.0.1，数据存在本机，不支持多人并发协作。
- 展示版对数据做了裁剪。`data/sources/laws/`（抓取缓存）与 `data/sources/repo/`（LawRefBook 快照）未入库，可以用 `scripts/fetch_statutes.py` 重建；完整法库以 4 个分片形式随仓库分发。

## 开发与维护

展示仓不手工维护副本。`scripts/sync_public.py` 会把私有源重新剥离、切片、打补丁、自检，再同步过来：

```bash
python scripts/sync_public.py --dry-run          # 只构建 + 自检，不落盘
python scripts/sync_public.py                    # 同步到工作区，人工 review 后自行提交
python scripts/sync_public.py --commit "发布 v1.1.3 展示版" --push
```

剥离规则（`STRIP_DIRS` / `STRIP_FILES`）和代码补丁（`scripts/showcase/patches/`）都在仓库里，能审计也能改。私有源码如果改到了补丁涉及的位置，脚本会直接失败并提示重建补丁，不会悄悄漏掉带授权校验的代码。

## 许可与第三方声明

本仓库没有附开源许可证（LICENSE），仅用于展示，不提供安装包与商业授权。

代码和法规数据来自完整产品 Contract LocalScan 1.1.2，剥离了授权校验（`cls/license.py`）、渠道与打包配置、密钥占位和运行期数据。规则引擎、检索、解析、导出和 385 条测试都是生产代码。截图取自 macOS 上运行本仓库代码的界面和导出件，示例合同为虚构主体。

第三方组件（Flask、jieba、pypdf、python-docx、reportlab、openpyxl、rapidocr 等）的许可证，以及中国法律文本、CISG、纽约公约、示范法、UNIDROIT 通则的数据来源，都列在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
