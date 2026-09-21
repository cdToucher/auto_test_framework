"""API 步骤执行器：构造请求 → 断言 → 变量捕获。"""
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..store.models import ApiExpect, ApiStep
from .asserts import MISSING, evaluate, get_path
from .env import find_unresolved, substitute

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
        method, raw_path = parts[0].upper(), parts[1]
        try:
            path = substitute(raw_path, self.variables)
            headers = substitute(step.headers, self.variables)
            body = substitute(step.body, self.variables) if step.body is not None else None
            expectations = [
                ApiExpect(
                    status=e.status,
                    status_op=e.status_op,
                    path=substitute(e.path, self.variables) if e.path else e.path,
                    op=e.op,
                    value=substitute(e.value, self.variables),
                    raw=substitute(e.raw, self.variables) if e.raw else e.raw,
                )
                for e in step.expect
            ]
        except Exception as e:
            return StepResult(
                step.call, False, f"配置错误: {e}", error_class="config",
            )
        # 未解析的 ${var} 原样透传给服务端只会产生误导性失败，发请求前拦截
        unresolved = find_unresolved([path, headers, body, [
            (e.path, e.value, e.raw) for e in expectations
        ]])
        if unresolved:
            return StepResult(
                step.call,
                False,
                f"配置错误: 变量未解析 {'、'.join(unresolved)}"
                "（检查 capture 是否产出该变量、环境 vars 拼写，或场景间变量隔离）",
                error_class="config",
            )
        resp = None
        last_err: Exception | None = None
        for attempt in range(step.retries + 1):
            try:
                resp = self.client.request(method, path, headers=headers, json=body)
                last_err = None
                break
            except httpx.HTTPError as e:
                last_err = e
            except Exception as e:
                # 非 HTTP 异常（URL非法/变量替换产物非法等）归配置类，不杀整个 run
                return StepResult(
                    step.call,
                    False,
                    f"配置错误: {e}",
                    error_class="config",
                )
        if last_err is not None or resp is None:
            return StepResult(
                step.call,
                False,
                f"环境异常(重试{step.retries}次): {last_err}",
                error_class="environment",
            )

        failures = [
            why
            for e in expectations
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
