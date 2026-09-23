"""API 步骤执行器：构造请求 → 断言 → 变量捕获。"""
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..store.models import ApiExpect, ApiStep
from .asserts import MISSING, evaluate, get_path
from .env import find_unresolved, substitute

_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

#: 失败时留存的响应体上限。报告会被提交进仓库、贴进 CI 产物，不能无界。
BODY_LIMIT = 4000
_SECRET_KEY = re.compile(
    r"(token|passwd|password|secret|api[_-]?key|cookie|authorization|credential|session)",
    re.I,
)


def _mask(value: Any) -> Any:
    """按键名屏蔽疑似凭据：值一律换成 ***，只保留结构。"""
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and _SECRET_KEY.search(k) else _mask(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def body_snippet(resp: httpx.Response) -> str:
    """实际返回的现场：断言消息只说"哪个字段不对"，这里给出整份响应。

    JSON 走解析后屏蔽（键名命中凭据样式的换值），非 JSON 原样截断；两者都限长。
    """
    try:
        text = json.dumps(_mask(resp.json()), ensure_ascii=False, default=str,
                          separators=(",", ":"))
        kind = "json"
    except (ValueError, UnicodeDecodeError):
        text = resp.text
        kind = "text"
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT] + f"…（已截断，原长 {len(text)} 字符）"
    return f"HTTP {resp.status_code} · {kind} · {text}"


@dataclass
class StepResult:
    """单个步骤的结果。

    detail 说明"哪条断言为什么不过"，response 只在失败时留一份实际返回（已脱敏截断）：
    光有断言消息的话，人还得重跑一遍才知道服务端到底返回了什么。
    """
    title: str
    passed: bool
    detail: str = ""
    error_class: str = "assertion"  # assertion | environment | config
    response: str = ""              # 失败时的实际返回（定位用；已通过则不保留）


@dataclass
class ApiExecutor:
    """执行 API 步骤，并维护同一场景内的共享变量。

    variables 是跨步骤的可写字典：capture 写入、`${var}` 读出。每个场景都要新建一份，
    否则上一个场景捕获的 token 会串进下一个场景。
    """
    client: httpx.Client
    variables: dict[str, Any] = field(default_factory=dict)

    def execute(self, step: ApiStep) -> StepResult:
        """替换变量 → 发请求（按 step.retries 重试）→ 逐条断言 → 捕获变量。

        三类失败归类不同，处置也不同：
        - config：call 格式非法、`${var}` 没解析出来、URL/替换产物异常。这类**不发请求**——
          带着未解析的 `${var}` 原样打到服务端，只会得到一个误导性的 4xx。
        - environment：httpx 异常且重试耗尽。只有环境类错误重试，断言失败不重试。
        - assertion：请求成功但 expect 不成立，detail 带实际值、response 带整份返回。

        所有 expect 一次判完再汇总（不是首错即停），一遍就能看全差在哪。
        场景没显式断言 status 时，HTTP >= 400 也算失败——否则接口报 500 而响应体恰好
        没有断言字段时，会被误判成通过。
        """
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
            # 断言消息与整份响应分开放：detail 给人看"哪条不对"，response 给人看"到底返回了什么"
            return StepResult(step.call, False, "; ".join(failures),
                              response=body_snippet(resp))

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
