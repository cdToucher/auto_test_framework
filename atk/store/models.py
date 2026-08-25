"""场景数据模型：YAML 结构的强类型表达。"""
from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class ApiExpect(BaseModel):
    """单条断言：status 或 响应 JSON 点路径。"""

    status: int | None = None
    path: str | None = None
    op: Literal["eq", "not_null"] = "eq"
    value: Any = None


class ApiStep(BaseModel):
    type: Literal["api"] = "api"
    call: str  # 形如 "POST /api/orders"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    expect: list[ApiExpect] = Field(default_factory=list)
    capture: dict[str, str] = Field(default_factory=dict)  # 变量名 -> 响应点路径
    retries: int = 1  # 仅环境类错误重试，断言失败不重试


class UiStep(BaseModel):
    type: Literal["ui"] = "ui"
    action: str
    expect: str | None = None  # 自然语言断言，M2 实现


Step = Union[ApiStep, UiStep]


def parse_expect_map(m: dict[str, Any]) -> list[ApiExpect]:
    """把 {status: 200, "data.orderNo": not_null} 展开为断言列表。"""
    rules: list[ApiExpect] = []
    for k, v in m.items():
        if k == "status":
            rules.append(ApiExpect(status=int(v)))
        elif v == "not_null":
            rules.append(ApiExpect(path=k, op="not_null"))
        else:
            rules.append(ApiExpect(path=k, op="eq", value=v))
    return rules


def parse_step(raw: dict) -> Step:
    """解析嵌套步骤 {api: {...}} / {ui: {...}}。"""
    if "api" in raw:
        d = dict(raw["api"])
        d["expect"] = parse_expect_map(d.get("expect") or {})
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
