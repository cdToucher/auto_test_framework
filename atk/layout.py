"""项目布局解析：atk 产物收束在单一 `.atk/` 目录，旧布局自动兼容。

两种布局：
- legacy（历史项目，如信飞/本仓库）：scenarios/ config/ reports/ fixtures/ 散在根目录。
  判定：项目根存在 scenarios/ 或 config/ 或 reports/ 任一即视为 legacy，保持原样不动。
- atk（init 新项目）：一切生成物在 .atk/ 下——
  .atk/{environments.yaml, modules.yaml, scenarios/, reports/runs/, skills/,
        last-run.json, manifest.json}
  场景 data: 引用相对场景库父目录解析，fixtures 自然落在 .atk/fixtures/。

所有 CLI 默认路径经这里解析；显式传入的 --root/--env-file/--runs-dir 等永远优先。
"""
from pathlib import Path

ATK_DIR = ".atk"
LEGACY_MARKERS = ("scenarios", "config", "reports")


def is_legacy(root: Path | str = ".") -> bool:
    """看根目录有没有 scenarios/ config/ reports/ 任一日目录，有即按旧布局处理。

    只要一个标记就够：老项目常常只提交了 scenarios/ 与 config/，reports/ 被 gitignore，
    要求三个都在会让它被误判成新布局、凭空多出 .atk/ 双份目录。
    """
    root = Path(root)
    return any((root / m).is_dir() for m in LEGACY_MARKERS)


def mode(root: Path | str = ".") -> str:
    """当前布局名（legacy / atk），报告与 atk agent 用它说明解析结果。"""
    return "legacy" if is_legacy(root) else "atk"


def scenarios_dir(root: Path | str = ".") -> Path:
    """场景库根目录。"""
    root = Path(root)
    return root / "scenarios" if is_legacy(root) else root / ATK_DIR / "scenarios"


def env_file(root: Path | str = ".") -> Path:
    """environments.yaml 位置（旧布局在 config/ 下）。"""
    root = Path(root)
    return root / "config" / "environments.yaml" if is_legacy(root) else root / ATK_DIR / "environments.yaml"


def modules_file(root: Path | str = ".") -> Path:
    """modules.yaml 位置；schedules.yaml 也按"它的同级目录"派生，两种布局都不用改代码。"""
    root = Path(root)
    return root / "config" / "modules.yaml" if is_legacy(root) else root / ATK_DIR / "modules.yaml"


def reports_dir(root: Path | str = ".") -> Path:
    """报告根目录（HTML / JUnit 落这里）。"""
    root = Path(root)
    return root / "reports" if is_legacy(root) else root / ATK_DIR / "reports"


def runs_dir(root: Path | str = ".") -> Path:
    """运行记录目录：每次 run 一个 <run_id>/run.yaml 子目录。"""
    return reports_dir(root) / "runs"


def skills_dir(root: Path | str = ".") -> Path:
    """技能正文目录。旧布局也固定放 .atk/ 下——它们是 atk 生成物，不参与布局判断。"""
    return Path(root) / ATK_DIR / "skills"


def manifest_file(root: Path | str = ".") -> Path:
    """init 生成物清单+内容指纹（purge 的安全依据）。同样恒在 .atk/ 下。"""
    return Path(root) / ATK_DIR / "manifest.json"


def last_run_file(root: Path | str = ".") -> Path:
    """`--last` 指针文件。恒在 .atk/ 下，与布局无关。"""
    return Path(root) / ATK_DIR / "last-run.json"
