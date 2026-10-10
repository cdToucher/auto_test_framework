"""人工审批账本：把"这条断言有人看过并认了"变成一份可校验的制品。

为什么必须有这个模块：`ai-generated` 这个 tag 是 AI 自己写进 YAML 的字符串，删掉它就等于
"已转正"，所以"AI 不得自批"这句话在代码里没有任何承载物。这里换成 ApprovalTests 的结构：

- oracle 是 `config/approvals.yaml` 里的一条记录，键是**断言面**的 sha256（不含注释、
  priority、tags 这些装饰性内容——改注释不该撤销审批，改断言必须重新审批）；
- 记录只能凭一枚在真实终端上铸出的一次性凭证写入（见 `mint` / `consume`）；
- 断言面变了 → 键变了 → 旧记录自然失效。

沿袭豁免（刻意的设计，而不是 fail-closed）：本次变更集**没碰过**的场景不要求历史记录，
否则接入这套机制的那一刻，全库既有场景会一起变成待评审。判据是
`rec.affected_files ∪ 工作区未提交文件`——新写但还没提交的文件同样算"碰过"。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from . import keystore

AI_NAME_RE = re.compile(r"(?i)\b(ai|a\.i|bot|agent|llm|assistant|claude|qoder|copilot)\b")
APPROVALS_FILE = "approvals.yaml"
REVIEWERS_FILE = "reviewers.yaml"
PENDING_DIR = "approvals"


class Approval(BaseModel):
    """一条"某人认过这份断言面"的记录。kind 留口给契约层（P7 的 contracts approve）。"""

    kind: str = "scenario"
    target: str = ""          # 场景文件路径（相对项目根）
    key: str = ""             # sha256:<断言面>
    by: str = ""
    at: str = ""
    token: str = ""           # 凭证的 sha256，只作追溯，不作校验依据
    note: str = ""
    sig: str = ""             # 仓库外密钥的 HMAC；没有效签名的条目一律不认


def approval_payload(a: "Approval") -> str:
    """参与签名的字段表示。note/at/by 全部入签：改任何一项都让签名作废。"""
    return keystore.canonical([a.kind, a.target, a.key, a.by, a.at, a.note])


def valid_approvals(path: Path | str) -> tuple[list["Approval"], list["Approval"]]:
    """分成"签名有效"与"签名无效"两组。

    无效的那组要能报出来：否则一条被手改过、或来自另一把密钥的记录只会让 gate
    显示"从没人批过"，谁也不知道账本里其实写着批过——那是最难查的一类误导。
    """
    good: list[Approval] = []
    bad: list[Approval] = []
    for a in load_approvals(path):
        (good if keystore.verify(approval_payload(a), a.sig) else bad).append(a)
    return good, bad


class Reviewers(BaseModel):
    humans: list[str] = Field(default_factory=list)
    allowlist_required: bool = False   # 名单文件存在即要求严格匹配


# ------------------------------------------------------------------ 断言面

def expect_surface(raw: Any) -> list[str]:
    """把一份场景 YAML 投影成"它断言了什么"的行列表。

    只取会改变判定结果的部件：接口、断言(path/op/value)、capture 的键名、UI 步骤的
    action+expect。刻意不含：注释、priority、tags、module、env、title。
    """
    if not isinstance(raw, dict):
        return []
    lines: list[str] = []
    for i, step in enumerate(raw.get("steps") or []):
        if not isinstance(step, dict):
            lines.append(f"{i}:?")
            continue
        api = step.get("api")
        if isinstance(api, dict):
            exp = api.get("expect")
            lines.append(f"{i}:api:{str(api.get('call') or '').strip()}:"
                         f"{_norm_expect(exp)}:"
                         f"{_norm_keys(api.get('capture'))}")
            continue
        ui = step.get("ui")
        if isinstance(ui, dict):
            lines.append(f"{i}:ui:{str(ui.get('action') or '').strip()}:"
                         f"{str(ui.get('expect') or '').strip()}")
            continue
        lines.append(f"{i}:unknown:{sorted(step)}")
    return lines


def _norm_expect(value: Any) -> str:
    """断言归一化：映射与列表两种写法、键序、空白都折成同一串。"""
    if isinstance(value, dict):
        return ";".join(
            f"{_norm_scalar(k)}={_norm_scalar(v)}"
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0])))
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(",".join(
                    f"{k}={_norm_scalar(item[k])}"
                    for k in ("path", "op", "value", "status", "status_op")
                    if k in item))
            else:
                parts.append(_norm_scalar(item))
        return ";".join(sorted(parts))
    return _norm_scalar(value)


def _norm_scalar(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return str(v).strip()


def _norm_keys(d: Any) -> str:
    if not isinstance(d, dict):
        return ""
    return ",".join(sorted(str(k) for k in d))


def expect_hash(path: Path | str) -> str:
    """场景文件断言面的指纹。读不到/解析不出来返回 ""，由调用方按"未批准"处理。"""
    import yaml

    p = Path(path)
    if not p.is_file():
        return ""
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines = expect_surface(raw)
    if not lines:
        return ""
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ------------------------------------------------------------------ 账本读写

def approvals_file(project: Path | str = ".") -> Path:
    from . import layout

    base = layout.modules_file(project).parent
    return base / APPROVALS_FILE


def reviewers_file(project: Path | str = ".") -> Path:
    from . import layout

    base = layout.modules_file(project).parent
    return base / REVIEWERS_FILE


def pending_dir(project: Path | str = ".") -> Path:
    from . import layout

    return layout.manifest_file(project).parent / PENDING_DIR


def load_approvals(path: Path | str) -> list[Approval]:
    import yaml

    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("approvals") or []
    out: list[Approval] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            out.append(Approval(**item))
    return out


def _rel(path: Path | str, project: Path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(Path(project).resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def write_approvals(path: Path | str, items: list[Approval]) -> None:
    import yaml

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"approvals": [i.model_dump() for i in items]}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def is_approved(*, target: Path | str, project: Path | str = ".",
                key: Optional[str] = None) -> bool:
    """这份断言面有没有被**真人**认过——只认带有效签名的条目。

    签名密钥在仓库外（见 atk/keystore.py），所以"往 approvals.yaml 里补一条"不再等于
    批过了。没有密钥时全部视为未批：那说明从没人在终端铸过凭证。
    """
    cur = key if key is not None else expect_hash(target)
    if not cur:
        return False
    rel = _rel(Path(target), Path(project))
    good, _bad = valid_approvals(approvals_file(project))
    return any(a.key == cur and a.target in (rel, str(target)) for a in good)


def needs_approval(*, target: Path | str, project: Path | str = ".",
                   touched: Iterable[str] = ()) -> tuple[bool, str]:
    """这份断言面有没有被**真人**认过。没批过就需要审批，没有例外。

    沿袭豁免（"早于基线存在就不用批"）已删除：它留下的洞是 AI 把新场景先
    commit、再以那次提交当基线跑 plan，未经任何人审的断言面直接过门禁。
    存量场景的一次性迁移通道是 `atk approvals mint --grandfather`（交互式
    终端、一次确认、逐份写签名审批），不是永远开一个"不批也能过"的口子。
    """
    if is_approved(target=target, project=project):
        return (False, "已批准")
    return (True, "该场景的断言面尚无人批准")



def pending_reviews(reports_dir: Path | str, project: Path | str = ".") -> list[dict]:
    """读评审日志，挑出"日志里说过批过、但账本里没有对应审批记录"的条目。

    `reports/draft-reviews.yaml` 以前是只写不读的哑文件：批了就是批了，谁也查不出
    "日志说批过但实际没生效"的落差。有了审批账本之后，这份日志才有了对账对象。
    """
    import yaml

    log = Path(reports_dir) / "draft-reviews.yaml"
    if not log.is_file():
        return []
    try:
        data = yaml.safe_load(log.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    recorded = {(a.kind, a.target) for a in load_approvals(approvals_file(project))}
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict) or str(item.get("verdict") or "") != "approve":
            continue
        rel = _rel(Path(str(item.get("file") or "")), Path(project))
        if ("scenario", rel) not in recorded:
            out.append({**item, "rel": rel})
    return out


# ------------------------------------------------------------------ 人工在场凭证

def human_present() -> bool:
    """人工在场的判据：stdin 与 stdout 都是终端。

    Agent 驱动的 shell 是管道，isatty() 为 False。这是整套"AI 不得自批"唯一的
    机械承载点，所以 mint 不提供 --yes 之类的旁路：留了旁路就等于没有这道门。
    """
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (ValueError, OSError):
        return False


def _pending_path(project: Path | str, token_id: str) -> Path:
    return pending_dir(project) / f"{token_id}.json"


def _token_variants(token: str) -> list[str]:
    t = token.strip()
    return [t] if t.startswith("appr-") else [f"appr-{t}", t]


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _tid(token: str) -> str:
    return _sha(token)[:16]


def grandfather(*, targets: list[tuple[str, str]], by: str, note: str = "",
                project: Path | str = ".") -> list[Approval]:
    """存量场景一次性批量转正：交互式终端里一次确认，逐份写入签名审批。

    沿袭豁免删除后，历史场景在下次 gate 前必须批过一次；对几十条存量场景，
    逐份 mint + review-draft 是不现实的，所以有这条一次性通道。它并不比单份
    审批弱：同样要求 human_present()（stdin 与 stdout 都是 TTY），AI/CI 驱动
    的 shell 一律 exit 2。确认前会把每份文件的断言面指纹列全，供人逐条核对。

    targets 是 [(相对路径, 断言面指纹)]；返回写入的条目。
    """
    if not human_present():
        raise RuntimeError(
            "批量转正必须在交互式终端里执行：非 TTY 环境（AI/CI 驱动的 shell）一律拒绝。"
            "请开发者本人在终端执行 atk approvals mint --grandfather。")
    import typer

    typer.echo(f"\n待转正存量场景 {len(targets)} 份：")
    for rel, key in targets:
        typer.echo(f"  - {rel}  {key[:18]}…")
    if not typer.confirm("以上断言面你已逐条看过并认可？"):
        raise RuntimeError("已取消：未确认则不写入任何审批")
    items = load_approvals(approvals_file(project))
    now = datetime.now().isoformat(timespec="seconds")
    written: list[Approval] = []
    for rel, key in targets:
        a = Approval(kind="scenario", target=rel, key=key, by=by,
                     note=note or "存量场景一次性批量转正", at=now, token="")
        a.sig = keystore.sign(approval_payload(a))
        items = [x for x in items
                 if not (x.kind == a.kind and x.target == a.target and x.key == a.key)]
        items.append(a)
        written.append(a)
    write_approvals(approvals_file(project), items)
    return written


def mint(*, target: str, key: str, project: Path | str = ".",
         display: str = "") -> tuple[str, str]:
    """在真实终端上铸一枚一次性凭证。返回 (明文码, 记录 id)。

    明文码只出现在终端上，落盘的只有 sha256 与绑定关系；核销一次即失效。
    """
    if not human_present():
        raise RuntimeError(
            "审批凭证必须在交互式终端里铸：非 TTY 环境（AI/CI 驱动的 shell）一律拒绝。"
            "请开发者本人在终端执行 atk approvals mint。")
    import typer

    typer.echo(f"\n待批准对象：{target}")
    typer.echo(f"断言面指纹：{key or '（无法计算）'}")
    if display:
        typer.echo(display)
    if not typer.confirm("以上断言你已逐条看过并认可？"):
        raise RuntimeError("已取消：未确认则不产生凭证")
    target = _rel(Path(target), Path(project))
    token = f"appr-{secrets.token_hex(8)}"
    d = pending_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    p = _pending_path(project, _tid(token))
    p.write_text(
        json.dumps({"target": target, "key": key, "sha256": _sha(token),
                    "minted_at": datetime.now().isoformat(timespec="seconds")}),
        encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    typer.echo(f"一次性凭证（核销后失效，请原样填入 --approval）：\n  {token}")
    return token, p.stem


def consume(*, token: str, target: str, key: str,
            project: Path | str = ".") -> tuple[bool, str]:
    """核销凭证。成功即删除，一枚凭证只能用一次、只对一个对象有效。"""
    if not (token or "").strip():
        return (False, "缺少 --approval 凭证：审批记录只能凭交互式终端铸出的一次性凭证写入")
    hit: Optional[tuple[str, Path]] = None
    for variant in _token_variants(token):
        p = _pending_path(project, _tid(variant))
        if p.is_file():
            hit = (variant, p)
            break
    if hit is None:
        return (False, "凭证无效、已过期或已被核销，请在终端重新 atk approvals mint")
    variant, p = hit
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        p.unlink(missing_ok=True)
        return (False, "凭证记录损坏，请重新 mint")
    if rec.get("sha256") != _sha(variant):
        return (False, "凭证校验失败")
    if rec.get("target") != target or rec.get("key") != key:
        return (False, f"凭证绑定的是 {rec.get('target')}，与本次审批对象不符")
    p.unlink(missing_ok=True)
    return (True, "")


def record_approval(*, target: str, key: str, by: str, note: str, token: str,
                    project: Path | str = ".", kind: str = "scenario",
                    require_present: bool = True) -> Approval:
    """写入一条审批。require_present=False 仅供测试注入，CLI 不暴露该开关。"""
    target = _rel(Path(target), Path(project))
    ok, why = consume(token=token, target=target, key=key, project=project)
    if not ok:
        raise RuntimeError(why)
    items = load_approvals(approvals_file(project))
    rel = _rel(Path(target), Path(project))
    approval = Approval(kind=kind, target=rel, key=key, by=by, note=note,
                        at=datetime.now().isoformat(timespec="seconds"),
                        token=_sha(token) if token else "")
    approval.sig = keystore.sign(approval_payload(approval))
    items = [a for a in items if not (a.kind == kind and a.target == rel and a.key == key)]
    items.append(approval)
    write_approvals(approvals_file(project), items)
    return approval


# ------------------------------------------------------------------ 评审人身份

def load_reviewers(path: Path | str) -> Reviewers:
    import yaml

    p = Path(path)
    if not p.is_file():
        return Reviewers()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return Reviewers()
    humans: list[str] = []
    for h in data.get("humans") or []:
        if isinstance(h, dict):
            for k in ("name", "aliases"):
                v = h.get(k)
                if isinstance(v, str):
                    humans.append(v.strip())
                elif isinstance(v, list):
                    humans.extend(str(x).strip() for x in v)
        elif isinstance(h, str):
            humans.append(h.strip())
    return Reviewers(humans=[h for h in humans if h], allowlist_required=True)


def check_reviewer(name: str, project: Path | str = ".",
                   author: Optional[str] = None) -> Optional[str]:
    """评审人身份校验。返回错误说明；None 表示通过。"""
    who = (name or "").strip()
    if not who:
        return "缺少 --by：审批记录必须落到具体的人"
    if AI_NAME_RE.search(who):
        return f"评审人 '{who}' 看起来不是人：AI/bot/agent 一类身份不得自批"
    if author and who == author.strip():
        return f"评审人不能是作者本人（{who}）"
    rev = load_reviewers(reviewers_file(project))
    if rev.allowlist_required and who not in rev.humans:
        return (f"评审人 '{who}' 不在 {reviewers_file(project)} 名单里；"
                "接入项目请登记真人")
    return None


def detect_author(path: Path | str, project: Path | str = ".") -> str:
    """场景作者判定。不看 tag（那是 AI 可写的字符串），看 git 事实：
    文件从未被提交过（新文件/被改过未提交）→ 视为本轮 AI 产出。
    """
    rel = _rel(Path(path), Path(project))
    try:
        tracked = subprocess.run(
            ["git", "-C", str(project), "ls-files", "--error-unmatch", rel],
            capture_output=True, text=True)
    except OSError:
        return ""
    return "ai" if tracked.returncode != 0 else ""
