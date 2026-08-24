"""场景执行编排：加载 → 逐场景执行 → 汇总报告。"""
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..store.loader import load_scenarios, select
from ..store.models import ApiStep, Priority, Scenario
from .api_executor import ApiExecutor, StepResult
from .env import load_env


@dataclass
class ScenarioResult:
    scenario: Scenario
    passed: bool
    error_class: str  # none | assertion | environment | ui_unsupported
    steps: list[StepResult] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class RunReport:
    env_name: str
    started_at: str
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return self.total - self.passed_count

    @property
    def environment_errors(self) -> int:
        return sum(1 for r in self.results if r.error_class == "environment")


class Runner:
    def __init__(
        self,
        env_file: Path | str = "config/environments.yaml",
        scenarios_root: Path | str = "scenarios",
    ):
        self.env_file = Path(env_file)
        self.scenarios_root = Path(scenarios_root)

    def run(
        self,
        env_name: str,
        module: str | None = None,
        tags: list[str] | None = None,
        priority: Priority | None = None,
        on_result=None,
    ) -> RunReport:
        env = load_env(self.env_file, env_name)
        scenarios = select(load_scenarios(self.scenarios_root), module, tags, priority)
        report = RunReport(
            env_name=env_name,
            started_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
        with httpx.Client(base_url=env.base_url, timeout=30) as client:
            executor = ApiExecutor(client, dict(env.vars))
            for sc in scenarios:
                result = self._run_one(executor, sc)
                report.results.append(result)
                if on_result:
                    on_result(result)
        return report

    @staticmethod
    def _run_one(executor: ApiExecutor, sc: Scenario) -> ScenarioResult:
        steps: list[StepResult] = []
        error_class = "none"
        for st in sc.steps:
            if isinstance(st, ApiStep):
                sr = executor.execute(st)
                steps.append(sr)
                if not sr.passed:
                    error_class = sr.error_class
                    break  # 场景内快速失败，后续步骤依赖前序变量
            else:
                steps.append(
                    StepResult(
                        title=f"[UI] {st.action}",
                        passed=False,
                        detail="UI 执行器 M2 提供",
                        error_class="ui_unsupported",
                    )
                )
                error_class = "ui_unsupported"
                break
        return ScenarioResult(
            scenario=sc,
            passed=bool(steps) and all(s.passed for s in steps),
            error_class=error_class,
            steps=steps,
        )
