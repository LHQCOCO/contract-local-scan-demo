# Agent 运行时（合同场景）

默认关闭。`config.json` 里 `agent.enabled=true` 才能调用 `POST /api/agent/review`；
`POST /api/review` 需显式传 `mode=agent`；不传 `mode` 完全走原有 rule 管线。

## 分层与依赖方向

```
宿主 app.py / tools.agent_review.py      ← 只做接线：解析、入队、轮询
        │
cls/scenarios/contract/                  ← 场景层：工具、角色、计划模板、渲染
        │  依赖 ↓，不得反向
cls/agent/                               ← 内核：types registry planner executor
        │                                   roles context evidence trace adapter eval
cls/（config parsing extraction risks kb revisions reporting store）
```

`cls/agent/` 不 import `cls/scenarios`，也不 import `cls/license`；`tests/test_agent_architecture.py` 用 AST 守住这条线。

## 工具（名称即计划里引用的名字）

| 名称 | 包装对象 | 副作用 |
| --- | --- | --- |
| `run_rules` | `risks.evaluate` + `extraction.extract/detect_type` | none |
| `locate_clause` | 原文定位（不新增业务逻辑） | none |
| `search_statute` | `KB.search_for_contract` / `KB.search` | none |
| `get_statute` | `KB.by_id` | none |
| `propose_revision` | 规则 basis 的条文原文（`revisions.build_revision`） | none |
| `export_report` | `reporting.build_pdf/docx/xlsx` | local_write |

## 计划

`s1 run_rules` → `s2 search_statute`（foreach high/medium）→ `s3 propose_revision`（同上一一对应）
→ `s4 reviewer` 汇总 → `s5 verifier` 逐条核查 → 可选 `s6 export_report` 导出。
依赖未满足记 `SKIPPED_DEP`；预算耗尽立即停，保留已完成产物（`stop_reason=BUDGET_EXCEEDED`，
`status=partial`）；计划结构非法（重复 id / 悬空依赖）返回 `stop_reason=PLAN_INVALID`。

## trace 事件

`run.start / plan.created / step.start / tool.call / tool.result / role.message / verify.result / step.end / run.end`

公共字段：`ts run_id seq event step_id role tool args_digest latency_ms tokens_in tokens_out ok error_code fallback`。
`verify.result` 逐条记录 `rule_id / verdict / reason_code`，可直接回答「哪条被降级、为什么」。
白名单落盘：参数只写 `sha256` 前 8 位，超长字符串写 `sha8:… len:N`，路径写 `path_sha8:…`；
正文、文件名、完整路径一律不落盘。样例见 `docs/trace-sample.jsonl`。

## 新增场景（如求职分析）

1. 建 `cls/scenarios/<name>/`：`tools.py` 提供 `TOOL_SPECS`，`manifest.py` 组装 `Scenario`（角色、`ChecklistPlanner`、渲染器）。
2. 在 `cls/scenarios/__init__.py` 里**显式** import 并登记到 `SCENARIOS`（禁止动态 import，避免 PyInstaller 漏模块）。
3. 宿主按 `SCENARIOS[name]` 取场景；内核无需改动。`Role.allowed_tools` 决定越权拦截。

## 已知边界

`LLMPlanner` 仅留接口（构造即抛 `NotImplementedError`）；本次未接真实 7B 模型，
角色默认走确定性回退（`role.message.fallback=true`）。
