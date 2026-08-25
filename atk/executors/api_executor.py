"""API 步骤执行器：构造请求 → 断言 → 变量捕获。"""
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..store.models import ApiStep
from .asserts import MISSING, evaluate, get_path
from .env import substitute

_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


@dataclass
class StepResult:
    title: str
    passed: bool
    detail: str = ""
    error_class: str = "assertion"  # assertion | environment | config


@dataclass
class ApiExecutor:
    client: httpx.Client
    variables: dict[str, Any] = field(default_factory=dict)

    def execute(self, step: ApiStep) -> StepResult:
        parts = step.call.split()
        if len(parts) != 2 or parts[0].upper() not in _VALID_METHODS:
            return StepResult(
                step.call,
                False,
                f"call 格式非法：'{step.call}'，应为 'METHOD /path'（如 'GET /ping'）",
                error_class="config",
            )
        method, path = parts[0].upper(), substitute(parts[1], self.variables)
        headers = substitute(step.headers, self.variables)
        body = substitute(step.body, self.variables) if step.body is not None else None
        resp = None
        last_err: Exception | None = None
        for attempt in range(step.retries + 1):
            try:
                resp = self.client.request(method, path, headers=headers, json=body)
                last_err = None
                break
            except httpx.HTTPError as e:
                last_err = e
        if last_err is not None or resp is None:
            return StepResult(
                step.call,
                False,
                f"环境异常(重试{step.retries}次): {last_err}",
                error_class="environment",
            )

        failures = [
            why
            for e in step.expect
            for ok in [evaluate(resp, e)]
            if not ok[0]
            for why in [ok[1]]
        ]
        if all(e.status is None for e in step.expect) and resp.status_code >= 400:
            failures.append(
                f"未显式断言 status，隐式要求 status<400，实际 {resp.status_code}"
            )
        if failures:
            preview = resp.text[:300]
            return StepResult(step.call, False, "; ".join(failures) + f" | resp: {preview}")

        warnings: list[str] = []
        try:
            body = resp.json()
        except ValueError:
            body = None
            warnings.append("响应非 JSON，capture 已跳过")
        if body is not None:
            for var, dotted in step.capture.items():
                val = get_path(body, dotted)
                if val is MISSING:
                    warnings.append(f"capture '{var}' 未命中路径 '{dotted}'")
                else:
                    self.variables[var] = val
        detail = f"{resp.status_code}"
        if warnings:
            detail += "；警告: " + "; ".join(warnings)
        return StepResult(step.call, True, detail)
