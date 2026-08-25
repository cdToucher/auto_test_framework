"""场景执行编排：加载 → 逐场景执行 → 汇总报告。"""
import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..store.loader import load_scenarios, select
from ..store.models import ApiStep, Priority, Scenario
from .api_executor import ApiExecutor, StepResult
from .env import EnvConfig, load_env


@dataclass
class ScenarioResult:
    scenario: Scenario
    passed: bool
    error_class: str  # none | assertion | environment | config | ui_unsupported | empty
    steps: list[StepResult] = field(default_factory=list)
    duration_ms: int = 0
    env: str = ""


@dataclass
class RunReport:
    env_name: str = ""
    started_at: str = ""
    results: list[ScenarioResult] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        """真实用例失败（断言/配置错误），环境受阻不计入。"""
        return sum(
            1 for r in self.results if not r.passed and r.error_class in ("assertion", "config")
        )

    @property
    def blocked_count(self) -> int:
        """未通过但非用例失败：环境异常 / UI 未支持 / 空场景。"""
        return self.total - self.passed_count - self.failed_count

    @property
    def environment_errors(self) -> int:
        return sum(1 for r in self.results if r.error_class == "environment")

    @property
    def exit_code(self) -> int:
        if self.failed_count or self.load_errors:
            return 1
        if self.blocked_count:
            return 2
        return 0


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
        env_name: str | None = None,
        module: str | None = None,
        tags: list[str] | None = None,
        priority: Priority | None = None,
        on_result=None,
    ) -> RunReport:
        """env_name 为 None 时，按各场景自身的 env 字段选择环境。"""
        scenarios, errors = load_scenarios(self.scenarios_root)
        report = RunReport(
            env_name=env_name or "(按场景)",
            started_at=dt.datetime.now().isoformat(timespec="seconds"),
            load_errors=errors,
        )
        selected = select(scenarios, module, tags, priority)
        clients: dict[str, httpx.Client] = {}
        envs: dict[str, EnvConfig] = {}
        try:
            for sc in selected:
                name = env_name or sc.env
                env = envs.get(name)
                if env is None:
                    env = load_env(self.env_file, name)
                    envs[name] = env
                client = clients.get(name)
                if client is None:
                    client = httpx.Client(
                        base_url=env.base_url, timeout=30, trust_env=env.trust_env
                    )
                    clients[name] = client
                t0 = time.perf_counter()
                result = self._run_one(ApiExecutor(client, dict(env.vars)), sc)
                result.duration_ms = int((time.perf_counter() - t0) * 1000)
                result.env = name
                report.results.append(result)
                if on_result:
                    on_result(result)
        finally:
            for c in clients.values():
                c.close()
        return report

    @staticmethod
    def _run_one(executor: ApiExecutor, sc: Scenario) -> ScenarioResult:
        steps: list[StepResult] = []
        error_class = "none"
        if not sc.steps:
            error_class = "empty"
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
