"""场景注册入口。

显式 import，保证 PyInstaller 静态分析能收集到模块；禁止动态扫描注册。
"""
from .base import Scenario
from .contract import manifest as contract_manifest
from .contract.manifest import CONTRACT_SCENARIO
from .jobsearch import manifest as jobsearch_manifest
from .jobsearch.manifest import JOBSEARCH_SCENARIO
from .kbqa import manifest as kbqa_manifest
from .kbqa.manifest import KBQA_SCENARIO

SCENARIOS = {
    "contract": CONTRACT_SCENARIO,
    "jobsearch": JOBSEARCH_SCENARIO,
    "kbqa": KBQA_SCENARIO,
}

__all__ = ["Scenario", "SCENARIOS", "CONTRACT_SCENARIO", "JOBSEARCH_SCENARIO",
           "KBQA_SCENARIO", "contract_manifest", "jobsearch_manifest",
           "kbqa_manifest"]
