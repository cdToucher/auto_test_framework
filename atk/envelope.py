"""统一的机器可读输出契约（所有 `--format json` 走这里）。

取向：AI 在 TUI/CLI 里触发 atk，靠"读完输出就知道下一条跑什么"推进，所以每个命令
都补同一组顶层键：

    schema_version  契约版本，字段语义变了就 +1
    cmd             命令名，便于日志里辨认是谁说的
    ok              结论（true/false）
    exit_code       与进程退出码一致：0 通过 · 1 失败/拦截 · 2 受阻/配置错
    next            可直接执行的 atk 命令列表，按序走（沿用 smoke/gate 原有形状）
    blockers        阻塞原因，非空先解阻塞再谈推进
    requires_human  该步需要人判断，AI 必须停下把 human_prompt 转给人
    human_prompt    给人看的问题

采用**平铺合并**而不是把结果塞进 data：CI 示例与 skill 已经在读 run_id、verdict、
intents_pending 这些顶层键，嵌套一层是破坏性变更；平铺只增键、不改不删旧键。
"""
from __future__ import annotations

import json

SCHEMA_VERSION = 1

#: 契约字段名：写文档/断言时用这一份，别在各命令里散着写字符串
FIELDS = ("schema_version", "cmd", "ok", "exit_code",
          "next", "blockers", "requires_human", "human_prompt")


def wrap(cmd: str, payload: dict, *, next_steps: list[str] | None = None,
         blockers: list[str] | None = None, ok: bool = True,
         exit_code: int | None = None, requires_human: bool = False,
         human_prompt: str | None = None) -> dict:
    """把命令自己的结果平铺进统一契约。

    payload 里已有的 next/blockers 会被尊重（record/gate 等按分支自己算），
    显式传参才覆盖；这样接入是加一层，不是改写各命令的分支逻辑。
    """
    merged = dict(payload)
    merged["blockers"] = list(blockers) if blockers is not None else list(merged.get("blockers") or [])
    if next_steps is not None:
        merged["next"] = list(next_steps)
    merged.setdefault("next", [])
    merged["schema_version"] = SCHEMA_VERSION
    merged["cmd"] = cmd
    merged["ok"] = bool(ok) and not merged["blockers"]
    if exit_code is None:
        # 顺序要紧：先定 ok，再推退出码，否则"有 blockers 但 exit 0"这种自相矛盾
        # 的组合会悄悄流到 CI。进程退出码与命令语义不同的地方必须显式传 exit_code。
        exit_code = 0 if merged["ok"] else (2 if merged["blockers"] else 1)
    merged["exit_code"] = exit_code
    merged["requires_human"] = bool(requires_human)
    # 恒在：机器读的时候不该为"这个键今天有没有"写分支
    merged["human_prompt"] = human_prompt
    return merged


def dumps(cmd: str, payload: dict, **hints) -> str:
    """wrap() 的 JSON 序列化：CLI 各命令 `--format json` 的统一出口。"""
    return json.dumps(wrap(cmd, payload, **hints), ensure_ascii=False, indent=2)
