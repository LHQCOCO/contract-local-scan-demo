"""求职决策命令行：手工 JD + 本地简历 → 匹配分析、改写建议与投递决策卡。

    python -m tools.jobsearch_agent --jd samples/jobs/jd_ai_agent.md \
        --resume samples/jobs/resume_sample.md --trace out.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="求职决策 Agent（W1）")
    parser.add_argument("--jd", required=True, help="JD 文本文件路径（手工粘贴后保存）")
    parser.add_argument("--resume", required=True, help="简历文件路径（md/txt/docx/pdf）")
    parser.add_argument("--trace", default="", help="trace.jsonl 输出路径")
    parser.add_argument("--save", action="store_true", help="写入本地投递记录")
    parser.add_argument("--model", default="", help="可选：启用本地模型做计划选择")
    args = parser.parse_args(argv)

    from cls.agent.runtime import AgentRuntime, config_budget
    from cls.scenarios.jobsearch.manifest import JOBSEARCH_SCENARIO, build_scenario

    jd_text = Path(args.jd).expanduser().read_text(encoding="utf-8", errors="replace")
    resume_text = Path(args.resume).expanduser().read_text(encoding="utf-8", errors="replace")

    adapter = None
    if args.model:
        from cls.agent.adapter import OllamaAdapter
        adapter = OllamaAdapter(model=args.model)

    scenario = build_scenario(adapter) if adapter is not None else JOBSEARCH_SCENARIO

    run_id = uuid.uuid4().hex[:12]
    runtime = AgentRuntime(scenario, adapter=adapter)
    run = runtime.run(
        {"jd_text": jd_text, "resume_text": resume_text,
         "save": bool(args.save), "run_id": run_id},
        budget=config_budget(),
        run_id=run_id,
        trace_path=args.trace or None,
    )
    print(json.dumps({
        "mode": "agent",
        "run_id": run.run_id,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "metrics": run.metrics,
        "summary": run.artifacts.get("summary", {}),
        "trace": str(run.trace_path),
    }, ensure_ascii=False, indent=2))
    return 0 if run.status in {"ok", "partial"} else 1


if __name__ == "__main__":
    sys.exit(main())
