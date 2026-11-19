"""命令行入口：与 Web 共用同一运行时。

    python -m tools.agent_review --file samples/x.docx --mode agent --trace out.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contract LocalScan 命令行审阅")
    parser.add_argument("--file", required=True, help="PDF / DOCX / TXT 路径")
    parser.add_argument("--mode", choices=["rule", "agent"], default="rule")
    parser.add_argument("--trace", default="", help="trace.jsonl 输出路径")
    parser.add_argument("--model", default="", help="可选：覆盖本地模型名")
    parser.add_argument("--export", action="store_true", help="Agent 模式下导出 PDF")
    args = parser.parse_args(argv)

    path = Path(args.file)
    data = path.read_bytes()

    if args.mode == "rule":
        import app

        result = app._run_pipeline(path.name, data, False)
        print(json.dumps({
            "mode": "rule", "score": result["score"], "level": result["level"],
            "findings": len(result["findings"]),
        }, ensure_ascii=False))
        return 0

    from cls import extraction, parsing
    from cls.agent.runtime import AgentRuntime, config_budget
    from cls.scenarios import CONTRACT_SCENARIO
    from cls.scenarios.contract.documents import DOCS

    doc = parsing.parse(path.name, data)
    doc_id = DOCS.add(doc["text"], path.name, {
        "ext": doc.get("ext", ""), "pages": doc.get("pages", 0),
        "size": doc.get("size", 0), "title_guess": doc.get("title_guess", ""),
        "scan_suspect": doc.get("scan_suspect", False),
        "ocr_used": doc.get("ocr_used", False),
    })
    adapter = None
    if args.model:
        from cls.agent.adapter import OllamaAdapter
        adapter = OllamaAdapter(model=args.model)

    runtime = AgentRuntime(CONTRACT_SCENARIO, adapter=adapter)
    run = runtime.run(
        {"doc_id": doc_id, "contract_type": extraction.detect_type(doc["text"]),
         "export": args.export, "fmt": "pdf"},
        budget=config_budget(),
        trace_path=args.trace or None,
        scopes=("export",) if args.export else (),
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
