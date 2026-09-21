"""环境配置加载与 ${var} / ${env:VAR} 替换。"""
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_VAR = re.compile(r"\$\{(\w+)\}")
_ENV_VAR = re.compile(r"\$\{env:([A-Za-z_]\w*)\}")
_UNRESOLVED = re.compile(r"\$\{[^}]*\}")


class EnvConfig(BaseModel):
    base_url: str
    vars: dict[str, Any] = {}
    trust_env: bool = False  # 被测环境必须经系统代理访问时置 true
    missing_env: list[str] = []  # ${env:VAR} 引用了未设置的环境变量（已按空串解析）


def load_env(config_path: Path | str, name: str) -> EnvConfig:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    if name not in data:
        raise KeyError(f"环境 '{name}' 未在 {config_path} 中定义")
    cfg = EnvConfig(**data[name])
    # 先于替换扫描 ${env:VAR} 引用，缺失变量显性告警而不是静默空串
    refs = [m.group(1) for m in _ENV_VAR.finditer(str(cfg.model_dump()))]
    cfg.missing_env = [r for r in dict.fromkeys(refs) if r not in os.environ]
    # vars 中的 ${env:NAME} 在加载期即解析，避免占位符透传到请求
    cfg.vars = {k: substitute(v, {}) for k, v in cfg.vars.items()}
    # base_url 同口径解析，使 CI 模板的 ${env:ATK_BASE_URL} 生效；
    # 缺失变量行为与 vars 一致（${env:} 缺失 -> ""，未知 ${var} 保留原串）
    cfg.base_url = substitute(cfg.base_url, {})
    return cfg


def substitute(node: Any, variables: dict) -> Any:
    """递归替换字符串中的 ${name} 与 ${env:NAME}；整串匹配时保留原始类型。"""
    if isinstance(node, str):
        node = _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), ""), node)
        m = _VAR.fullmatch(node)
        if m:
            return variables.get(m.group(1), node)
        return _VAR.sub(lambda mm: str(variables.get(mm.group(1), mm.group(0))), node)
    if isinstance(node, dict):
        return {k: substitute(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, variables) for v in node]
    return node


def find_unresolved(node: Any) -> list[str]:
    """收集替换后仍残留的 ${...} 占位符（拼写错的 ${var} 会原样透传）。"""
    found: list[str] = []
    if isinstance(node, str):
        found.extend(m.group(0) for m in _UNRESOLVED.finditer(node))
    elif isinstance(node, dict):
        for v in node.values():
            found.extend(find_unresolved(v))
    elif isinstance(node, (list, tuple)):
        for v in node:
            found.extend(find_unresolved(v))
    return list(dict.fromkeys(found))
