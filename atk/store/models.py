"""场景数据模型：YAML 结构的强类型表达。"""
import math
import re
from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------- 断言操作符

#: 符号别名 -> 规范操作符（长的在前，避免 "<=" 被 "<" 抢先匹配）
OP_SYMBOLS: dict[str, str] = {
    "==": "eq",
    "!=": "ne",
    "<=": "lte",
    ">=": "gte",
    "<": "lt",
    ">": "gt",
}

#: 关键字操作符，写作 "关键字: 值"
OP_KEYWORDS: tuple[str, ...] = (
    "contains",
    "not_contains",
    "regex",
    "len",
    "type",
    "in",
    "not_in",
    "startswith",
    "endswith",
)

#: 空值操作符，写作 not_null / is_null
OP_NULLARY: tuple[str, ...] = ("not_null", "is_null")

#: 全部合法操作符
ALL_OPS: frozenset[str] = frozenset(
    {"eq", "ne", "lt", "lte", "gt", "gte"}
    | set(OP_KEYWORDS)
    | set(OP_NULLARY)
)

_SYM_RE = re.compile(r"^\s*(<=|>=|!=|==|<|>)\s*(.*)$", re.S)
_KW_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)


def _coerce_scalar(s: str) -> Any:
    """把 '90' / 'true' / 'null' 转成对应类型；失败返回原串（保留前导零等语义）。"""
    t = s.strip()
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return s


def _parse_in_list(rest: str) -> list[Any]:
    """解析 in/not_in 的取值："[a, b]" 或 "a, b"。"""
    inner = rest.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    if not inner.strip():
        return []
    return [_coerce_scalar(x.strip()) for x in inner.split(",")]


def _parse_len(rest: str) -> Any:
    """解析 len 的取值："3" 或 "1..5"（闭区间）。"""
    t = rest.strip()
    if ".." in t:
        lo, _, hi = t.partition("..")
        return {"min": _coerce_scalar(lo), "max": _coerce_scalar(hi)}
    return _coerce_scalar(t)


def _length_bound(value: Any) -> float | None:
    """将长度边界校验为有限的非负整数，保留原始值供报告展示。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return number


def parse_expect_value(v: Any) -> tuple[str, Any]:
    """把 expect 的值解析为 (op, value)。

    纯字面量（含字符串）一律按 eq 处理，保证向后兼容：
        "done"          -> ("eq", "done")
        "not_null"      -> ("not_null", None)
        "< 90"          -> ("lt", "90")
        "contains: 成功" -> ("contains", "成功")
        "len: 1..5"     -> ("len", {"min": 1, "max": 5})
        "\\<literal"    -> ("eq", "<literal")   以反斜杠开头转义为字面量
    """
    if not isinstance(v, str):
        return "eq", v
    if v.startswith("\\"):
        return "eq", v[1:]
    stripped = v.strip()
    if stripped in OP_NULLARY:
        return stripped, None
    m = _SYM_RE.match(v)
    if m:
        return OP_SYMBOLS[m.group(1)], m.group(2).strip()
    m = _KW_RE.match(v)
    if m and m.group(1) in OP_KEYWORDS:
        op, rest = m.group(1), m.group(2).strip()
        if op in ("in", "not_in"):
            return op, _parse_in_list(rest)
        if op == "len":
            return op, _parse_len(rest)
        return op, rest
    return "eq", v


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class ApiExpect(BaseModel):
    """单条断言：status 或 响应 JSON 点路径。

    op 取值见 ALL_OPS；raw 保存 YAML 原始表达式，仅用于报告展示。
    """

    status: Any = None
    status_op: str = "eq"
    path: str | None = None
    op: str = "eq"
    value: Any = None
    raw: str = ""

    @field_validator("op")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in ALL_OPS:
            raise ValueError(f"未知断言操作符 {v!r}，可用：{', '.join(sorted(ALL_OPS))}")
        return v

    @field_validator("status_op")
    @classmethod
    def _check_status_op(cls, v: str) -> str:
        if v not in ALL_OPS:
            raise ValueError(
                f"未知 status 断言操作符 {v!r}，可用：{', '.join(sorted(ALL_OPS))}"
            )
        return v

    @model_validator(mode="after")
    def _check_len_value(self) -> "ApiExpect":
        if self.op != "len":
            return self
        if not isinstance(self.value, dict):
            if _length_bound(self.value) is None:
                raise ValueError("len 的期望值必须是非负整数或 min/max 区间")
            return self

        unknown = set(self.value) - {"min", "max"}
        if unknown:
            raise ValueError(f"len 区间仅支持 min/max，存在未知字段：{sorted(unknown)}")
        lo, hi = self.value.get("min"), self.value.get("max")
        lo_num = _length_bound(lo) if lo is not None else None
        hi_num = _length_bound(hi) if hi is not None else None
        if lo is not None and lo_num is None:
            raise ValueError("len 区间 min 必须是非负整数")
        if hi is not None and hi_num is None:
            raise ValueError("len 区间 max 必须是非负整数")
        if lo_num is None and hi_num is None:
            raise ValueError("len 区间至少需要 min 或 max")
        if lo_num is not None and hi_num is not None and lo_num > hi_num:
            raise ValueError("len 区间的 min 不能大于 max")
        return self


class ApiStep(BaseModel):
    type: Literal["api"] = "api"
    call: str  # 形如 "POST /api/orders"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    expect: list[ApiExpect] = Field(default_factory=list)
    capture: dict[str, str] = Field(default_factory=dict)  # 变量名 -> 响应点路径
    retries: int = Field(default=1, ge=0, le=10)  # 仅环境类错误重试，断言失败不重试


class UiStep(BaseModel):
    type: Literal["ui"] = "ui"
    action: str
    expect: str | None = None  # 自然语言断言，M2 实现


Step = Union[ApiStep, UiStep]


def parse_expect_map(m: dict[str, Any]) -> list[ApiExpect]:
    """把 {status: 200, "data.orderNo": not_null, "data.amount": "< 90"} 展开为断言列表。"""
    rules: list[ApiExpect] = []
    for k, v in m.items():
        if k == "status":
            op, val = parse_expect_value(v)
            if op in OP_NULLARY:
                raise ValueError(f"status 不支持 {op}，如需断言请改用具体状态码")
            rules.append(
                ApiExpect(
                    status=_coerce_status(val), status_op=op, raw=f"status {op} {val}"
                )
            )
            continue
        op, val = parse_expect_value(v)
        rules.append(ApiExpect(path=k, op=op, value=val, raw=f"{k}: {v}"))
    return rules


def parse_expect_list(items: list[Any]) -> list[ApiExpect]:
    """解析列表式 expect：-[{path, op, value}] / -[{status, op}]。

    op 支持符号别名（"==" "<" ">=" 等），统一归一化为规范名。
    """
    rules: list[ApiExpect] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"expect[{i}] 必须是映射，实际 {type(it).__name__}")
        d = dict(it)
        explicit_op = d.pop("op", None)
        if "status" in d:
            raw_status = d.pop("status")
            # 未显式给 op 时，值里可能自带操作符（如 "< 400"）
            if explicit_op is None and isinstance(raw_status, str):
                op, val = parse_expect_value(raw_status)
            else:
                op, val = _normalize_op(explicit_op or "eq", i), raw_status
            if op in OP_NULLARY:
                raise ValueError(f"expect[{i}]：status 不支持 {op}，请改用具体状态码")
            val = _coerce_status(val)
            d.update(status=val, status_op=op, raw=f"status {op} {val}")
        else:
            raw_value = d.get("value")
            if explicit_op is None and isinstance(raw_value, str):
                op, val = parse_expect_value(raw_value)
            else:
                op, val = _normalize_op(explicit_op or "eq", i), raw_value
            d["op"] = op
            d["value"] = val
            d.setdefault("raw", f"{d.get('path')}: {op} {val}")
        rules.append(ApiExpect(**d))
    return rules


def _coerce_status(val: Any) -> Any:
    """status 是纯数字字符串时转 int（兼容旧行为，也让比较运算更稳）。"""
    if isinstance(val, str) and val.strip().lstrip("-").isdigit():
        return int(val.strip())
    return val


def _normalize_op(op: Any, index: int) -> str:
    """把符号别名（==/< />=）归一化成规范操作符名；未知原样返回交给模型校验。"""
    if not isinstance(op, str):
        return op
    return OP_SYMBOLS.get(op.strip(), op.strip())


def parse_expect(raw: Any) -> list[ApiExpect]:
    """统一入口：expect 支持映射式（简洁）与列表式（无歧义）。"""
    if raw is None:
        return []
    if isinstance(raw, dict):
        return parse_expect_map(raw)
    if isinstance(raw, list):
        return parse_expect_list(raw)
    raise ValueError(f"expect 必须是映射或列表，实际 {type(raw).__name__}")


def parse_step(raw: dict) -> Step:
    """解析嵌套步骤 {api: {...}} / {ui: {...}}。"""
    if "api" in raw:
        d = dict(raw["api"])
        d["expect"] = parse_expect(d.get("expect"))
        return ApiStep(**d)
    if "ui" in raw:
        return UiStep(**raw["ui"])
    raise ValueError(f"未知步骤类型: {sorted(raw)}")


class Scenario(BaseModel):
    scenario: str
    module: str = "default"
    priority: Priority = Priority.P1
    tags: list[str] = Field(default_factory=list)
    env: str = "local"
    data: str | None = None  # fixtures 文件路径，变量合并优先级 env < fixture < capture
    file: str = ""  # 来源路径，由 loader 填充
    steps: list[Step] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict, file: str = "") -> "Scenario":
        raw = dict(raw)
        raw["steps"] = [parse_step(s) for s in raw.get("steps", [])]
        obj = cls(**raw)
        obj.file = file
        return obj
