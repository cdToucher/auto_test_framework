"""场景执行编排：加载 → 逐场景执行 → 汇总报告。"""
import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..store.loader import load_scenarios, select
from ..store.models import ApiStep, Priority, Scenario
from .api_executor import ApiExecutor, StepResult
from .env import EnvConfig, load_env, substitute


@dataclass
class ScenarioResult:
    """单个场景的执行结果。

    error_class 是全套口径的枢纽：同一次 run 里，"失败"、"受阻"、"待实测"是三个
    不同去向（前者算 exit 1，后者分别算 exit 2 和"交给 gate 判"），不能合并成 pass/fail。
    """
    scenario: Scenario
    passed: bool
    # none           通过
    # assertion      断言失败（用例真实失败）
    # environment    环境异常（网络/连接/超时）
    # config         配置错误（call 格式、变量、fixture）
    # ui_pending     UI 步骤待 AI 浏览器实测，用 atk record 回填（不是失败）
    # skipped        --skip-ui 后无可执行步骤（不计入结果）
    error_class: str
    steps: list[StepResult] = field(default_factory=list)
    duration_ms: int = 0
    env: str = ""


@dataclass
class RunReport:
    """一次 run 的汇总。计数口径只在这里定义，CLI / JSON / HTML 报告共用，
    避免每个出口各自算一套"失败数"。"""
    env_name: str = ""
    started_at: str = ""
    results: list[ScenarioResult] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    env_warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """本次执行的场景数（含受阻与待实测）。"""
        return len(self.results)

    @property
    def passed_count(self) -> int:
        """全部步骤断言成立的场景数。"""
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        """真实用例失败（断言/配置错误），环境受阻不计入。"""
        return sum(
            1 for r in self.results if not r.passed and r.error_class in ("assertion", "config")
        )

    @property
    def pending_ui_count(self) -> int:
        """UI 步骤待 AI 实测：既非失败也非受阻，等 record 回填后才定性。"""
        return sum(1 for r in self.results if r.error_class == "ui_pending")

    @property
    def skipped_count(self) -> int:
        """空场景或 --skip-ui 后无可执行步骤，完全不计入结论。"""
        return sum(1 for r in self.results if r.error_class == "skipped")

    @property
    def blocked_count(self) -> int:
        """未通过且非用例失败、非待实测、非跳过：环境异常 / UI 未知错误。"""
        return (
            self.total
            - self.passed_count
            - self.failed_count
            - self.pending_ui_count
            - self.skipped_count
        )

    @property
    def environment_errors(self) -> int:
        """纯环境异常数。它是 blocked_count 的子集——受阻还包含归类不明的异常。"""
        return sum(1 for r in self.results if r.error_class == "environment")

    @property
    def exit_code(self) -> int:
        """进程退出码：1 用例失败或有加载错误，2 受阻，0 其余。

        UI 待实测不算本次执行的失败：定性由 atk gate 检查意图是否已 record 回填，
        否则一次纯 API 冒烟会被无关的 UI 意图拖成 exit 1。
        """
        if self.failed_count or self.load_errors:
            return 1
        if self.blocked_count:
            return 2
        return 0


class Runner:
    """场景执行编排器：加载场景 → 选择 → 按 env 建 client → 逐步执行 → 汇总。

    env 与 httpx.Client 按环境名缓存复用：一个 run 里多个场景常指向同一环境，
    重复建连会把认证/限流类问题伪装成用例失败。
    """
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
        skip_ui: bool = False,
        extra_vars: dict[str, str] | None = None,
    ) -> RunReport:
        """env_name 为 None 时，按各场景自身的 env 字段选择环境。

        extra_vars（如 --set k=v）压过 env vars 与 fixture，但 capture 运行时
        提取值仍最高——保证"命令行为临时参数服务，真实响应覆盖其后步骤"。
        """
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
                    if env.missing_env:
                        report.env_warnings.append(
                            f"环境 '{name}' 的 ${{env:VAR}} 引用的环境变量未设置（按空串解析）: "
                            + ", ".join(env.missing_env)
                        )
                client = clients.get(name)
                if client is None:
                    client = httpx.Client(
                        base_url=env.base_url, timeout=30, trust_env=env.trust_env
                    )
                    clients[name] = client
                variables = dict(env.vars)
                if sc.data:
                    import yaml as _yaml

                    def _config_fail(msg: str):
                        result = ScenarioResult(
                            scenario=sc,
                            passed=False,
                            error_class="config",
                            steps=[
                                StepResult(
                                    f"fixtures:{sc.data}",
                                    False,
                                    msg,
                                    error_class="config",
                                )
                            ],
                        )
                        result.duration_ms = 0
                        report.results.append(result)
                        if on_result:
                            on_result(result)

                    fx = Path(sc.data)
                    if not fx.is_absolute():
                        fx = self.scenarios_root.parent / sc.data
                    try:
                        fx_resolved = fx.resolve()
                        root_resolved = self.scenarios_root.parent.resolve()
                    except Exception as e:
                        _config_fail(f"fixture 路径非法: {sc.data}（{e}）")
                        continue
                    if not fx_resolved.is_relative_to(root_resolved):
                        _config_fail(f"fixture 越界已拒绝: {sc.data}")
                        continue
                    if not fx_resolved.is_file():
                        _config_fail(f"fixture 文件不存在: {sc.data}")
                        continue
                    try:
                        fixture_vars = (
                            _yaml.safe_load(fx_resolved.read_text(encoding="utf-8")) or {}
                        )
                    except Exception as e:
                        _config_fail(f"fixture 解析失败: {sc.data}（{e}）")
                        continue
                    if not isinstance(fixture_vars, dict):
                        _config_fail(f"fixture 顶层必须是映射: {sc.data}")
                        continue
                    # fixture 内同样支持 ${env:VAR}，避免敏感值只得写进场景文件
                    variables.update(substitute(fixture_vars, variables))
                if extra_vars:
                    variables.update(extra_vars)
                t0 = time.perf_counter()
                result = self._run_one(ApiExecutor(client, variables), sc, skip_ui)
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
    def _run_one(
        executor: ApiExecutor, sc: Scenario, skip_ui: bool = False
    ) -> ScenarioResult:
        steps: list[StepResult] = []
        error_class = "none"
        if not sc.steps:
            return ScenarioResult(scenario=sc, passed=False, error_class="skipped")
        for st in sc.steps:
            if isinstance(st, ApiStep):
                sr = executor.execute(st)
                steps.append(sr)
                if not sr.passed:
                    error_class = sr.error_class
                    break  # 场景内快速失败，后续步骤依赖前序变量
            elif skip_ui:
                continue  # 显式跳过 UI 步骤，不计入结论
            else:
                steps.append(
                    StepResult(
                        title=f"[UI] {st.action}",
                        passed=False,
                        detail="待 AI 浏览器实测，用 atk record 回填结论",
                        error_class="ui_pending",
                    )
                )
                error_class = "ui_pending"
                break
        if not steps:
            # --skip-ui 后无任何可执行步骤
            return ScenarioResult(scenario=sc, passed=False, error_class="skipped")
        return ScenarioResult(
            scenario=sc,
            passed=bool(steps) and all(s.passed for s in steps),
            error_class=error_class,
            steps=steps,
        )
