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
    root = Path(root)
    return any((root / m).is_dir() for m in LEGACY_MARKERS)


def mode(root: Path | str = ".") -> str:
    return "legacy" if is_legacy(root) else "atk"


def scenarios_dir(root: Path | str = ".") -> Path:
    root = Path(root)
    return root / "scenarios" if is_legacy(root) else root / ATK_DIR / "scenarios"


def env_file(root: Path | str = ".") -> Path:
    root = Path(root)
    return root / "config" / "environments.yaml" if is_legacy(root) else root / ATK_DIR / "environments.yaml"


def modules_file(root: Path | str = ".") -> Path:
    root = Path(root)
    return root / "config" / "modules.yaml" if is_legacy(root) else root / ATK_DIR / "modules.yaml"


def reports_dir(root: Path | str = ".") -> Path:
    root = Path(root)
    return root / "reports" if is_legacy(root) else root / ATK_DIR / "reports"


def runs_dir(root: Path | str = ".") -> Path:
    return reports_dir(root) / "runs"


def skills_dir(root: Path | str = ".") -> Path:
    return Path(root) / ATK_DIR / "skills"


def atk_use_file(root: Path | str = ".") -> Path:
    """AI 说明书正文，两种布局都固定在 .atk/atk_use.md。

    不放项目根：根级条目被 test_layout_purge 限定为 .atk/ AGENTS.md .gitignore 三件，
    说明书属于"生成物"，和 last-run.json 一样收在 .atk/ 下。
    """
    return Path(root) / ATK_DIR / "atk_use.md"


def manifest_file(root: Path | str = ".") -> Path:
    return Path(root) / ATK_DIR / "manifest.json"


def last_run_file(root: Path | str = ".") -> Path:
    return Path(root) / ATK_DIR / "last-run.json"
