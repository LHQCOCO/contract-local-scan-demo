# PR：Agent 运行时内核 + 合同审阅场景（默认关闭）

## 摘要

在现有 rule 管线旁新增一层与业务解耦的 Agent 运行时，并在其上实现合同审阅场景。
默认关闭：`config.json` 的 `agent.enabled=false`；`POST /api/review` 不传 `mode` 时行为与改造前完全一致。
现有 `_run_pipeline`、`cls/risks.py` 的规则计算、`cls/license.py`、`cls/netguard.py`、`packaging/_cythonize.py` 均未改动。

## 改动清单

新增：

- `cls/agent/`：`types schema registry planner executor roles context evidence trace adapter eval runtime`
- `cls/scenarios/base.py`、`cls/scenarios/__init__.py`（显式登记场景）、`cls/scenarios/contract/{documents,tools,roles,manifest}.py`
- `tools/agent_review.py`：`python -m tools.agent_review --file <pdf> --mode agent --trace <out.jsonl>`
- `docs/agent-runtime.md`、`docs/trace-sample.jsonl`（真实运行、已脱敏）
- `tests/test_agent_{registry,executor,evidence_trace,roles,contract,architecture}.py`

修改：

- `app.py`：新增 `POST /api/agent/review`（202 + `run_id`，默认要求 `agent.enabled=true`）、`GET /api/agent/trace/<run_id>`；`/api/review` 增加可选 `mode`（缺省 `rule`）
- `cls/config.py`：新增 `agent.{enabled,model,budget}` 缺省值；旧配置文件缺该键时自动补齐
- `.gitignore`：忽略 `data/agent_traces/`、`data/agent_memory.db*`

## 基线对比

| 指标 | 改造前（worktree `7298460`） | 本分支 |
| --- | --- | --- |
| `pytest --collect-only -q` | 422 | **471** |
| `pytest -q` | 422 passed | **471 passed / 0 failed** |
| rule 模式，58,136 字符文本（20 次中位数） | 65.2 ms | 66.4 ms（+1.8%，在 ±5% 抖动内） |
| rule 模式，单页 PDF（5 次中位数） | 8.5 ms | 9.0 ms（同样在抖动范围内） |

`_run_pipeline` 内部无改动，耗时差异来自测量抖动；两版均在同一台机器、同一数据目录、同一批样本上交替测量。

Agent 模式实测：

- `samples/采购框架合同.pdf`（真实 PDF）：`status=ok`、`stop_reason=COMPLETED`，5 步、29 次工具调用、127 ms，2/2 `confirmed`，每条 span 均能在原文比对成功；trace 111 条事件（见 `docs/trace-sample.jsonl`）。
- 合成 ~10 页合同（5,051 字）：`status=ok`，23 次工具调用，运行 143 ms；进程冷启动 13.3 s（知识库加载占绝大部分）。

`RunResult.status` 取值 `ok / partial / failed`；`stop_reason` 取值 `COMPLETED / BUDGET_EXCEEDED / PLAN_INVALID`。
`POST /api/agent/review` 在 `agent.enabled=false` 时返回 403；`GET /api/agent/trace/<run_id>` 只回放脱敏事件。

## 未做

- `LLMPlanner` 只留接口（构造即 `NotImplementedError`），本次未接真实模型；7B 模型下的 ≤90 s 指标未验证（Ollama 未启动，全部走确定性回退）。
- 未实现求职分析场景，只保留 `Scenario` 契约与 `SCENARIOS` 登记点。
- 未引入任何第三方依赖、向量库、WebSocket 或多智能体自由对话。

## 如何验证

```bash
python3 -m pytest -q
python3 -m pytest --collect-only -q
python3 -m tools.agent_review --file samples/采购框架合同.pdf --mode agent --trace /tmp/t.jsonl
python3 -m tools.agent_review --file samples/采购框架合同.pdf --mode rule   # 默认路径不变
```
