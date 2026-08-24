"""断言求值：status 比较、点路径取值、eq/not_null。"""
from typing import Any

import httpx


class _Missing:
    def __repr__(self):
        return "<MISSING>"


MISSING = _Missing()


def get_path(data: Any, dotted: str) -> Any:
    """按点路径取值，数字段视为数组下标；缺失返回 MISSING。"""
    cur = data
    for seg in str(dotted).split("."):
        try:
            cur = cur[int(seg)] if seg.isdigit() else cur[seg]
        except (KeyError, IndexError, TypeError):
            return MISSING
    return cur


def evaluate(response: httpx.Response, expect) -> tuple[bool, str]:
    """返回 (是否通过, 失败原因)。expect 为 ApiExpect。"""
    if expect.status is not None:
        if response.status_code != expect.status:
            return False, f"status 期望 {expect.status} 实际 {response.status_code}"
    if expect.path is None:
        return True, ""
    body = response.json() if response.content else {}
    actual = get_path(body, expect.path)
    if expect.op == "not_null":
        ok = actual is not MISSING and actual not in (None, "")
        return ok, "" if ok else f"{expect.path} 为空或缺失"
    if actual is MISSING:
        return False, f"{expect.path} 缺失"
    ok = actual == expect.value
    return ok, "" if ok else f"{expect.path} 期望 {expect.value!r} 实际 {actual!r}"
