"""环境配置加载与 ${var} 替换。"""
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_VAR = re.compile(r"\$\{(\w+)\}")


class EnvConfig(BaseModel):
    base_url: str
    vars: dict[str, Any] = {}
    trust_env: bool = False  # 被测环境必须经系统代理访问时置 true


def load_env(config_path: Path | str, name: str) -> EnvConfig:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    if name not in data:
        raise KeyError(f"环境 '{name}' 未在 {config_path} 中定义")
    return EnvConfig(**data[name])


def substitute(node: Any, variables: dict) -> Any:
    """递归替换字符串中的 ${name}；整串匹配时保留原始类型。"""
    if isinstance(node, str):
        m = _VAR.fullmatch(node)
        if m:
            return variables.get(m.group(1), node)
        return _VAR.sub(lambda mm: str(variables.get(mm.group(1), mm.group(0))), node)
    if isinstance(node, dict):
        return {k: substitute(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, variables) for v in node]
    return node
