"""API 步骤执行器：构造请求 → 断言 → 变量捕获。"""
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..store.models import ApiStep
from .asserts import MISSING, evaluate, get_path
from .env import substitute


@dataclass
class StepResult:
    title: str
    passed: bool
    detail: str = ""
    error_class: str = "assertion"  # assertion | environment


@dataclass
class ApiExecutor:
    client: httpx.Client
    variables: dict[str, Any] = field(default_factory=dict)

    def execute(self, step: ApiStep) -> StepResult:
        method, _, raw_path = step.call.partition(" ")
        path = substitute(raw_path.strip(), self.variables)
        try:
            resp = self.client.request(
                method.upper(),
                path.strip(),
                headers=substitute(step.headers, self.variables),
                json=substitute(step.body, self.variables) if step.body is not None else None,
            )
        except httpx.HTTPError as e:
            return StepResult(step.call, False, f"环境异常: {e}", error_class="environment")

        failures = [
            why
            for e in step.expect
            for ok in [evaluate(resp, e)]
            if not ok[0]
            for why in [ok[1]]
        ]
        if not step.expect and resp.status_code >= 400:
            failures.append(f"无显式断言，隐式要求 status<400，实际 {resp.status_code}")
        if failures:
            preview = resp.text[:300]
            return StepResult(
                step.call, False, "; ".join(failures) + f" | resp: {preview}"
            )

        for var, dotted in step.capture.items():
            val = get_path(resp.json(), dotted)
            if val is not MISSING:
                self.variables[var] = val
        return StepResult(step.call, True, f"{resp.status_code}")
