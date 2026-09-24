"""审批与流水的签名密钥。

为什么需要：`config/approvals.yaml` 与 `reports/runs/<id>/events.yaml` 都在仓库里，
而仓库里的任何文件 AI 都能写。上一版只检查"记录在不在、指纹对不对"，于是自批的
距离从"删一个 tag"变成"多写一个 YAML 字段"——本质没变。签名把这条线挪到仓库外：
密钥在 `~/.atk/approval.key`（或由 `ATK_APPROVAL_KEY` 注入给 CI），伪造需要能读写
项目目录之外的位置，而不是在本项目里多写几行。

诚实的边界声明：这不是对抗"主机已被完全控制"的模型。本机单人工具的现实威胁是
"Agent 在项目内自由写文件"，这条线正好卡住它；能读 `$HOME` 的攻击者仍然能伪造。

没有密钥时一律判为无效（fail-closed）：那意味着从没有人真在终端批过。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

KEY_ENV = "ATK_APPROVAL_KEY"
KEY_FILE = "approval.key"
ALGO = "v1"


def key_file(home: Path | str | None = None) -> Path:
    root = Path(home or Path.home())
    return root / ".atk" / KEY_FILE


def _from_env() -> str:
    return (os.environ.get(KEY_ENV) or "").strip()


def available() -> bool:
    return bool(_from_env()) or key_file().is_file()


def load_or_create() -> bytes:
    """取密钥；没有就铸一把（0600，仓库外）。只允许 mint 侧调用。"""
    inline = _from_env()
    if inline:
        return inline.encode("utf-8")
    p = key_file()
    if p.is_file():
        data = p.read_bytes().strip()
        if data:
            return data
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    token = secrets.token_hex(32)
    p.write_text(token, encoding="utf-8")
    os.chmod(p, 0o600)
    return token.encode("utf-8")


def load() -> bytes | None:
    """只读取，绝不创建。校验侧（gate）用它：没密钥就没有一条记录算数。"""
    inline = _from_env()
    if inline:
        return inline.encode("utf-8")
    p = key_file()
    if not p.is_file():
        return None
    data = p.read_bytes().strip()
    return data or None


def canonical(fields: list[str]) -> str:
    """把参与签名的字段拼成唯一表示。分隔符不允许出现在字段里，避免拼接歧义。

    字段值里出现 `|` 会破坏无歧义性（`a|b` 与 `a,b` 之类可被重排），所以做百分号式
    转义而不是直接 join。
    """
    parts = []
    for f in fields:
        s = "" if f is None else str(f)
        parts.append(s.replace("%", "%25").replace("|", "%7c"))
    return f"{ALGO}|" + "|".join(parts)


def sign(payload: str) -> str:
    return hmac.new(load_or_create(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(payload: str, sig: str) -> bool:
    key = load()
    if key is None or not (sig or "").strip():
        return False
    want = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig.strip())


def missing_key_hint() -> str:
    return (f"签名密钥不可用（{key_file()} 不存在且未设置 {KEY_ENV}）："
            "所有审批与流水都按无效处理；开发者在终端执行 atk approvals mint 会自动建钥，"
            "CI 用 ATK_APPROVAL_KEY 注入同一把钥")
