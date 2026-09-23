"""最近一次运行记录的指针。

AI Agent 编排 plan→run→record→gate 时，需要把 run_id 从一条命令的输出
搬运到下一条命令的参数里，多意图时极易串号。本模块把 run_id 落到
`<项目根>/.atk/last-run.json`，后续命令用 --last 直接取用，不再手工拼接。
"""
import json
from pathlib import Path
from typing import Any

LAST_RUN_REL = Path(".atk") / "last-run.json"


def last_run_path(project: Path | str = ".") -> Path:
    """`--last` 指针文件位置（与 layout.last_run_file 同一处，改动要两边同步）。"""
    return Path(project) / LAST_RUN_REL


def write_last_run(project: Path | str, run_id: str, **meta: Any) -> Path:
    """写入最近运行指针，返回文件路径。meta 附加 base/head/env/title 等上下文。"""
    data = {"run_id": run_id}
    data.update(meta)
    p = last_run_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_last_run(project: Path | str = ".") -> dict[str, Any] | None:
    """读取最近运行指针；文件缺失或损坏一律返回 None（不抛异常）。"""
    p = last_run_path(project)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("run_id"):
        return None
    return data


def resolve_run_id(
    project: Path | str, run_id: str | None, use_last: bool = False
) -> str:
    """解析 run_id：--last 优先读指针，否则用显式传入值。

    二者都缺时抛 LookupError，由调用方转成友好提示与退出码 2。
    """
    if use_last:
        last = read_last_run(project)
        if last is None:
            raise LookupError(
                f"未找到最近运行记录（{last_run_path(project)} 不存在或损坏），"
                "请先执行 atk plan 或 atk smoke，或显式传入 run_id"
            )
        return str(last["run_id"])
    if run_id:
        return run_id
    raise LookupError("缺少 run_id；请显式传入，或加 --last 使用最近一次运行记录")
