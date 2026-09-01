"""LLM 输出落地：YAML 提取、结构校验、去重、草稿文件写入。

生成的文件是"AI 起草稿"：自动注入 `ai-generated` 标签与溯源头注释，
绝不覆盖已有文件；expect 断言仍需人工评审。
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from ..store.models import Scenario

GENERATED_TAG = "ai-generated"


def extract_yaml_blocks(text: str) -> list[str]:
    """从 LLM 回复中提取 fenced yaml 代码块。

    无围栏时，仅当整段回复看起来是一个场景文档（首个非注释行为 scenario:）
    才按单文档兜底，避免把纯说明文字误当 YAML。
    """
    blocks = re.findall(r"```(?:ya?ml)\s*\n(.*?)```", text, flags=re.DOTALL)
    if blocks:
        return blocks
    stripped = text.strip()
    first_line = next((ln for ln in stripped.splitlines() if ln.strip()), "")
    if first_line.lstrip("# ").startswith("scenario:"):
        return [stripped]
    return []


@dataclass
class GenResult:
    """单个生成场景的校验/落盘结果。"""

    name: str = ""
    module: str = ""
    ok: bool = False
    reason: str = ""
    path: str = ""


def _validate(
    raw_text: str,
    existing_names: set[str],
    allowed_modules: set[str],
) -> tuple[dict | None, str]:
    """校验单块 YAML 文本，返回 (raw dict, 错误说明)。通过时错误说明为空。"""
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        return None, f"YAML 解析失败: {str(e).splitlines()[0][:120]}"
    if not isinstance(raw, dict):
        return None, "根节点不是映射"
    if not raw.get("scenario"):
        return None, "缺少 scenario 名称"
    if not raw.get("steps"):
        return None, "steps 为空"
    module = str(raw.get("module", ""))
    if allowed_modules and module not in allowed_modules:
        return None, f"module '{module}' 不在本次影响面内"
    if str(raw["scenario"]) in existing_names:
        return None, f"场景名重复: {raw['scenario']}"
    try:
        Scenario.from_raw(raw)  # pydantic 结构校验（expect/step 类型）
    except Exception as e:
        return None, f"结构校验失败: {str(e).splitlines()[0][:160]}"
    return raw, ""


def _inject_provenance(raw: dict, ctx: dict, commits_desc: str) -> str:
    tags = list(dict.fromkeys([*(raw.get("tags") or []), GENERATED_TAG]))
    raw["tags"] = tags
    header = (
        f"# AI 起草稿（atk gen），expect 断言需人工评审后方可入库门禁\n"
        f"# 变更区间: {ctx['base']}...{ctx['head']}\n"
        f"# 关联提交: {commits_desc}\n"
        f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    body = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100)
    return header + body


def _free_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.yaml"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}.yaml"
        n += 1
    return candidate


def write_drafts(
    blocks: list[str],
    ctx: dict,
    scenarios_root: Path | str = "scenarios",
    existing_names: set[str] | None = None,
) -> list[GenResult]:
    """逐块校验并写入合法草稿；非法块跳过并记录原因。

    文件命名 scenarios/<module>/gen-<head短哈希>-<序号>.yaml，绝不覆盖既有文件。
    """
    existing_names = existing_names or set()
    allowed_modules = {str(m) for m in ctx.get("affected_modules", [])}
    commits_desc = "；".join(
        f"{c['short']} {c['subject']}" for c in ctx.get("commits", [])[:5]
    )
    results: list[GenResult] = []
    seq = 0
    for block in blocks:
        raw, err = _validate(block, existing_names, allowed_modules)
        if raw is None:
            results.append(GenResult(ok=False, reason=err))
            continue
        existing_names.add(str(raw["scenario"]))
        seq += 1
        module = str(raw.get("module", "default"))
        target_dir = Path(scenarios_root) / module
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = f"gen-{ctx.get('head_short', 'work')}-{seq}"
        path = _free_path(target_dir, stem)
        path.write_text(
            _inject_provenance(raw, ctx, commits_desc), encoding="utf-8"
        )
        results.append(
            GenResult(
                name=str(raw["scenario"]),
                module=module,
                ok=True,
                path=str(path),
            )
        )
    return results
