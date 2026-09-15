"""AI 草稿评审转正：approve 去 tag，reject 移走留档，全程记日志。

草稿 = scenarios 下 tags 含 `ai-generated` 的场景文件。转正即去掉该 tag；
未评审草稿参与执行时 gate 拦截（见 cli._do_gate）。
"""
import datetime as dt
import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel

DRAFT_TAG = "ai-generated"
REVIEW_LOG = "draft-reviews.yaml"
REJECTED_DIR = "rejected"


class DraftReview(BaseModel):
    file: str
    by: str = ""
    verdict: str = "approve"
    note: str = ""
    at: str = ""


def is_draft(path: Path | str) -> bool:
    """文件存在且为含 ai-generated tag 的场景映射时为 True；其余一律 False。"""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    tags = raw.get("tags") or []
    return isinstance(tags, list) and DRAFT_TAG in tags


def _append_log(entry: DraftReview, reports_dir: Path | str) -> None:
    log = Path(reports_dir) / REVIEW_LOG
    items: list = []
    if log.exists():
        try:
            loaded = yaml.safe_load(log.read_text(encoding="utf-8")) or []
            items = loaded if isinstance(loaded, list) else []
        except Exception:
            items = []
    items.append(entry.model_dump())
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _strip_draft_tag_linewise(text: str) -> str | None:
    """逐行去掉 tags 中的 ai-generated，保留注释与排版。

    只支持两种常见写法：行内 `tags: [a, b]` 与块列表 `tags:\\n  - a`。
    返回新文本；无法安全处理时返回 None，由调用方退回整体重写。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    removed = False
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)tags\s*:\s*(.*)$", line)
        if not m:
            out.append(line)
            i += 1
            continue
        indent, rest = m.group(1), m.group(2).strip()
        if indent:
            # 只处理场景顶层 tags；请求 body 等嵌套数据也可能合法地使用同名字段。
            out.append(line)
            i += 1
            continue
        if rest.startswith("["):
            close = rest.rfind("]")
            if close < 0:
                return None
            inner = rest[1:close]
            tail = rest[close + 1 :]  # 保留 "]" 之后的内容（行尾注释等）
            items = [x.strip() for x in inner.split(",") if x.strip()]
            kept = [x for x in items if x.strip("\"'") != DRAFT_TAG]
            if len(kept) == len(items):
                return None
            removed = True
            if kept:
                eol = "\n" if line.endswith("\n") else ""
                out.append(f"{indent}tags: [{', '.join(kept)}]{tail}{eol}")
            i += 1
            continue
        # 块列表
        out.append(line)
        i += 1
        while i < len(lines):
            mm = re.match(r"^(\s*)-\s*(.*)$", lines[i])
            if not mm:
                break
            if mm.group(2).strip().strip("\"'") == DRAFT_TAG:
                removed = True
                i += 1
                continue
            out.append(lines[i])
            i += 1
    return "".join(out) if removed else None


def _strip_draft_tag(text: str) -> str:
    """去 tag 优先走保注释的逐行改写，失败才退回 YAML 重写。"""
    try:
        edited = _strip_draft_tag_linewise(text)
    except Exception:
        edited = None
    if edited is not None:
        try:
            data = yaml.safe_load(edited)
        except yaml.YAMLError:
            data = None
        # 校验：仍是合法映射，且 tag 确实被去掉
        if isinstance(data, dict) and DRAFT_TAG not in (data.get("tags") or []):
            return edited
    raw = yaml.safe_load(text) or {}
    tags = raw.get("tags") or []
    tags = [t for t in tags if t != DRAFT_TAG] if isinstance(tags, list) else []
    if tags:
        raw["tags"] = tags
    else:
        raw.pop("tags", None)
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


def _unique(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    cand = dest_dir / name
    n, stem, suffix = 2, Path(name).stem, Path(name).suffix
    while cand.exists():
        cand = dest_dir / f"{stem}-{n}{suffix}"
        n += 1
    return cand


def review_draft(
    path: Path | str,
    by: str,
    verdict: str,
    note: str = "",
    reports_dir: Path | str = "reports",
) -> str:
    """评审单份草稿。approve 去 tag 就地转正；reject 移到 reports/rejected。

    返回人类可读结论。verdict 非法或非草稿抛 ValueError（防 API 直调
    误传 verdict 走 reject 分支移走文件）。
    """
    if verdict not in ("approve", "reject"):
        raise ValueError(f"verdict 必须是 approve|reject， got: {verdict!r}")
    p = Path(path)
    if not is_draft(p):
        raise ValueError(f"不是AI草稿（含{ DRAFT_TAG } tag 的场景文件）: {p}")
    now = dt.datetime.now().isoformat(timespec="seconds")
    if verdict == "approve":
        p.write_text(_strip_draft_tag(p.read_text(encoding="utf-8")), encoding="utf-8")
        _append_log(
            DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
            reports_dir,
        )
        return f"已转正：{p}（去掉 {DRAFT_TAG} tag）"
    dest = _unique(Path(reports_dir) / REJECTED_DIR, p.name)
    shutil.move(str(p), str(dest))
    _append_log(
        DraftReview(file=str(p), by=by, verdict=verdict, note=note, at=now),
        reports_dir,
    )
    return f"已移走：{p} -> {dest}"
