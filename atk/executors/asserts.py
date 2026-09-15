"""断言求值：status 比较、点路径取值、操作符比较。

YAML 写法（映射式，简洁）：

    expect:
      status: 200
      data.status: done              # 等价 eq
      data.orderNo: not_null
      data.amount: "< 90"            # 比较
      data.msg: "contains: 成功"
      data.mobile: "regex: ^1[3-9]\\d{9}$"
      data.items: "len: 3"
      data.state: "in: [pending, paid]"

列表式（无解析歧义，适合复杂值）：

    expect:
      - { path: data.amount, op: lte, value: 90 }
      - { path: data.items,  op: len, value: { min: 1, max: 10 } }

字面量以反斜杠开头可转义，避免被识别为操作符。
"""
import math
import re
from typing import Any

import httpx


class _Missing:
    def __repr__(self):
        return "<MISSING>"


MISSING = _Missing()

#: type 操作符支持的类型名
_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "string": str,
    "int": int,
    "float": float,
    "num": (int, float),
    "number": (int, float),
    "bool": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
}


def get_path(data: Any, dotted: str) -> Any:
    """按点路径取值，数字段视为数组下标；缺失返回 MISSING。"""
    cur = data
    for seg in str(dotted).split("."):
        try:
            cur = cur[int(seg)] if seg.isdigit() else cur[seg]
        except (KeyError, IndexError, TypeError):
            return MISSING
    return cur


def _num(v: Any) -> float | None:
    """尽力转数值；bool 不算数值（避免 True == 1 的误判）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _sized(v: Any) -> int | None:
    try:
        return len(v)
    except TypeError:
        return None


def _length_bound(v: Any) -> int | None:
    """将运行时替换后的长度边界转换为非负整数。"""
    number = _num(v)
    if number is None or not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _eq(a: Any, b: Any) -> bool:
    """相等判定：先直接比，再退化为数值比（兼容 "00123" vs 123）。"""
    if a == b:
        return True
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and na == nb


def _contains(actual: Any, expected: Any) -> bool:
    """包含：字符串走子串，list/tuple/dict 走成员，其余为 False。"""
    try:
        return expected in actual
    except TypeError:
        return False


def compare(op: str, actual: Any, expected: Any) -> tuple[bool, str]:
    """返回 (是否通过, 失败原因)。actual 可能为 MISSING。"""
    if op == "not_null":
        ok = actual is not MISSING and actual not in (None, "")
        return ok, "" if ok else "为空或缺失"
    if op == "is_null":
        ok = actual is MISSING or actual is None
        return ok, "" if ok else f"应为 null，实际 {actual!r}"
    if actual is MISSING:
        return False, "字段缺失"

    if op in ("eq", "ne"):
        ok = _eq(actual, expected)
        if op == "ne":
            ok = not ok
        return ok, "" if ok else f"期望 {expected!r}"

    if op in ("lt", "lte", "gt", "gte"):
        a, b = _num(actual), _num(expected)
        if a is None or b is None:
            return False, f"无法做数值比较（实际 {actual!r}，期望 {expected!r}）"
        ok = {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]
        return ok, "" if ok else f"期望 {op} {expected!r}"

    if op in ("contains", "not_contains"):
        ok = _contains(actual, expected)
        if op == "not_contains":
            ok = not ok
        return ok, "" if ok else f"期望{'不' if op == 'not_contains' else ''}包含 {expected!r}"

    if op in ("in", "not_in"):
        vals = expected if isinstance(expected, (list, tuple, set)) else [expected]
        ok = any(_eq(actual, v) for v in vals)
        if op == "not_in":
            ok = not ok
        return ok, "" if ok else f"期望{'不' if op == 'not_in' else ''}属于 {list(vals)!r}"

    if op == "regex":
        try:
            ok = re.search(str(expected), str(actual)) is not None
        except re.error as e:
            return False, f"正则表达式非法：{e}"
        return ok, "" if ok else f"期望匹配 {expected!r}"

    if op == "len":
        n = _sized(actual)
        if n is None:
            return False, f"无法取长度：{actual!r}"
        if isinstance(expected, dict):
            unknown = set(expected) - {"min", "max"}
            if unknown:
                return False, f"len 区间存在未知字段：{sorted(unknown)}"
            lo, hi = expected.get("min"), expected.get("max")
            lo_num = _length_bound(lo) if lo is not None else None
            hi_num = _length_bound(hi) if hi is not None else None
            if lo is not None and lo_num is None:
                return False, f"len 区间 min 必须是非负整数，实际 {lo!r}"
            if hi is not None and hi_num is None:
                return False, f"len 区间 max 必须是非负整数，实际 {hi!r}"
            if lo_num is None and hi_num is None:
                return False, "len 区间至少需要 min 或 max"
            if lo_num is not None and hi_num is not None and lo_num > hi_num:
                return False, "len 区间的 min 不能大于 max"
            if lo_num is not None and n < lo_num:
                return False, f"长度 {n} 小于下限 {lo}"
            if hi_num is not None and n > hi_num:
                return False, f"长度 {n} 超过上限 {hi}"
            return True, ""
        exp = _length_bound(expected)
        if exp is None:
            return False, f"len 的期望值必须是非负整数或区间，实际 {expected!r}"
        return n == exp, "" if n == exp else f"期望长度 {exp}"

    if op == "type":
        want = _TYPE_MAP.get(str(expected).strip().lower())
        if want is None:
            return False, (
                f"未知类型名 {expected!r}，可用：{', '.join(sorted(_TYPE_MAP))}"
            )
        name = str(expected).strip().lower()
        if name == "int":
            ok = isinstance(actual, int) and not isinstance(actual, bool)
        elif name in ("num", "number"):
            ok = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        else:
            ok = isinstance(actual, want)
        return ok, "" if ok else f"期望类型 {expected!r}"

    if op in ("startswith", "endswith"):
        s = str(actual)
        ok = s.startswith(str(expected)) if op == "startswith" else s.endswith(str(expected))
        return ok, "" if ok else f"期望 {op} {expected!r}"

    return False, f"未知操作符 {op!r}"


def describe(expect) -> str:
    """把断言渲染成人类可读表达式，用于报告展示。"""
    if getattr(expect, "raw", ""):
        return expect.raw
    if expect.status is not None:
        return f"status {getattr(expect, 'status_op', 'eq')} {expect.status}"
    if expect.op == "not_null":
        return f"{expect.path} 非空"
    return f"{expect.path} {expect.op} {expect.value!r}"


def evaluate(response: httpx.Response, expect) -> tuple[bool, str]:
    """返回 (是否通过, 失败原因)。expect 为 ApiExpect。

    not_null 语义：非 MISSING 且不为 None/空串；[]/{}/0 视为"非空"。
    """
    expr = describe(expect)
    if expect.status is not None:
        ok, why = compare(
            getattr(expect, "status_op", "eq"), response.status_code, expect.status
        )
        if not ok:
            return False, f"{expr}（实际 status {response.status_code}{'，' + why if why else ''}）"
    if expect.path is None:
        return True, ""
    try:
        body = response.json() if response.content else {}
    except ValueError:
        return False, f"{expr} 无法断言：响应不是 JSON（{response.text[:120]!r}）"
    actual = get_path(body, expect.path)
    ok, why = compare(expect.op, actual, expect.value)
    if ok:
        return True, ""
    actual_desc = "<缺失>" if actual is MISSING else repr(actual)
    return False, f"{expr}（实际 {expect.path}={actual_desc}{'，' + why if why else ''}）"
