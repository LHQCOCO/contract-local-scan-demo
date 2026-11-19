"""合同场景清单：工具、角色、计划模板与渲染器。"""
from __future__ import annotations

from ...agent.planner import ChecklistPlanner
from ...agent.types import Step
from ..base import Scenario
from .roles import REVIEWER_ROLE, VERIFIER_ROLE, ContractRoleRunner
from .tools import TOOL_SPECS


def build_task(inputs: dict) -> dict:
    return {
        "doc_id": inputs.get("doc_id", ""),
        "contract_type": inputs.get("contract_type", ""),
        "export": bool(inputs.get("export")),
        "fmt": inputs.get("fmt", "pdf"),
        "run_id": inputs.get("run_id", ""),
    }


def plan_template(task: dict) -> list[Step]:
    """run_rules → 逐条检索 → 逐条修订建议 → 汇总 → 核查 →（可选）导出。"""
    steps = [
        Step("s1", "", tool="run_rules", args={"doc_id": "{task.doc_id}"}),
        Step("s2", "", tool="search_statute",
             args={"query": "{item.title} {item.quote}",
                   "contract_type": "{task.contract_type}", "top_k": 5},
             depends_on=("s1",), foreach="s1.findings[severity=high|medium]",
             max_attempts=2),
        Step("s3", "", tool="propose_revision",
             args={"doc_id": "{task.doc_id}", "rule_id": "{item.rule_id}",
                   "span": "{item.span}"},
             depends_on=("s1",), foreach="s1.findings[severity=high|medium]"),
        Step("s4", "reviewer", args={"doc_id": "{task.doc_id}"}, depends_on=("s1",)),
        Step("s5", "verifier", args={}, depends_on=("s4",)),
    ]
    if task.get("export"):
        steps.append(Step("s6", "", tool="export_report",
                          args={"doc_id": "{task.doc_id}", "fmt": "{task.fmt}"},
                          depends_on=("s5",)))
    return steps


def render(ctx) -> dict:
    results = ctx.state.get("results", {})
    base = results.get("s1") or {}
    verdict = results.get("s5") or {}
    return {
        "doc_id": base.get("doc_id", ""),
        "score": base.get("score", 0),
        "level": base.get("level", ""),
        "contract_type": base.get("contract_type", ""),
        "confirmed": verdict.get("confirmed", 0),
        "downgraded": verdict.get("downgraded", 0),
        "rejected": verdict.get("rejected", 0),
        "verdicts": verdict.get("verdicts", []),
    }


CONTRACT_SCENARIO = Scenario(
    name="contract",
    title="合同审阅",
    version="1",
    tools=tuple(TOOL_SPECS),
    roles={"reviewer": REVIEWER_ROLE, "verifier": VERIFIER_ROLE},
    planner=ChecklistPlanner(plan_template),
    task_builder=build_task,
    renderer=render,
    role_runner_factory=lambda registry, adapter: ContractRoleRunner(registry, adapter),
)
