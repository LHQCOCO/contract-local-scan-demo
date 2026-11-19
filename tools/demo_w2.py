"""W2 演示：跑一次 Agent 审阅并打印 trace 摘要，再演示 EvidenceChecker 打回一条错误引用。

    CLS_DATA_DIR=/tmp/cls-run PYTHONPATH=. python3 tools/demo_w2.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from cls import extraction, parsing
from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.runtime import AgentRuntime, config_budget
from cls.agent.trace import Trace
from cls.agent.types import Budget
from cls.kb import KB
from cls.scenarios import CONTRACT_SCENARIO
from cls.scenarios.contract.documents import DOCS
from cls.scenarios.contract.roles import ContractRoleRunner

SAMPLE = Path("samples/软件开发服务合同.txt")
TRACE = Path("/tmp/w2-demo-trace.jsonl")


def pause(text: str, seconds: float = 0.8) -> None:
    print(text, flush=True)
    time.sleep(seconds)


def main() -> int:
    data = SAMPLE.read_bytes()
    doc = parsing.parse(SAMPLE.name, data)
    doc_id = DOCS.add(doc["text"], SAMPLE.name, {"title_guess": doc.get("title_guess", "")})

    pause(f"[1/2] Agent 审阅 {SAMPLE.name}")
    runtime = AgentRuntime(CONTRACT_SCENARIO)
    run = runtime.run(
        {"doc_id": doc_id, "contract_type": extraction.detect_type(doc["text"]),
         "export": False, "fmt": "pdf"},
        budget=config_budget(),
        trace_path=TRACE,
    )
    events = [json.loads(x) for x in TRACE.read_text(encoding="utf-8").splitlines() if x.strip()]
    counts = Counter(e.get("event", "") for e in events)
    verdicts = Counter(e.get("verdict", "") for e in events if e.get("event") == "verify.result")

    pause(f"  run_id={run.run_id} status={run.status} stop_reason={run.stop_reason}", 1.4)
    pause(f"  steps={run.metrics.get('steps')} tools={run.metrics.get('tool_calls')} "
          f"trace_events={len(events)}", 1.4)
    keep = ["run.start", "step.start", "tool.call", "verify.result", "run.end"]
    pause("  事件：" + " ".join(f"{k}={counts[k]}" for k in keep if counts.get(k)), 1.4)
    pause("  核查结论：" + " ".join(f"{k or 'skip'}={v}" for k, v in verdicts.items()), 1.4)
    starts = [e for e in events if e.get("event") == "step.start"]
    for e in starts[:8]:
        pause(f"    {e.get('step_id'):<7} -> {e.get('tool')}", 0.35)
    if len(starts) > 8:
        pause(f"    ... 另有 {len(starts) - 8} 个步骤", 0.8)
    pause(f"  trace: {TRACE}", 1.6)
    print()
    pause("[2/2] 把一条引用不存在法条的「改写」交给 EvidenceChecker")
    text = doc["text"]
    candidates = [s.strip() for s in re.split(r"[。\n]", text) if len(s.strip()) >= 15]
    quote = next((s for s in candidates if "付款" in s or "违约" in s), candidates[0])
    start = text.find(quote)
    real_id = KB.articles[0]["id"]
    draft = {"doc_id": doc_id, "findings": [
        {"rule_id": "R05", "severity": "high", "quote": quote, "span": {"start": start},
         "law_ids": [real_id], "top5": [real_id]},
        {"rule_id": "R05-改写", "severity": "high", "quote": quote, "span": {"start": start},
         "law_ids": ["law-does-not-exist"], "top5": ["law-does-not-exist"]},
    ]}
    reg = ToolRegistry()
    CONTRACT_SCENARIO.register_tools(reg)
    ctx = RunContext("demo-reject", Trace(Path("/tmp/w2-demo-reject.jsonl"), "demo-reject"),
                     Budget(), state={})
    out = ContractRoleRunner(reg).run("verifier", {"draft": draft}, ctx)
    pause(f"  原文片段：{quote}", 1.6)
    for v in out["verdicts"]:
        pause(f"  {v['rule_id']:<10} verdict={v['verdict']:<9} reason_code={v['reason_code']}", 1.2)
    rejected, seen = [], set()
    for e in ctx.trace.dump():
        if e.get("event") == "verify.result" and e.get("verdict") == "reject" \
                and e.get("seq") not in seen:
            seen.add(e.get("seq"))
            rejected.append(e)
    for e in rejected:
        pause(f"  trace 回放：seq={e.get('seq')} event={e.get('event')} "
              f"rule_id={e.get('rule_id')}", 0.8)
        pause(f"              verdict={e.get('verdict')} "
              f"reason_code={e.get('reason_code')}", 3.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
