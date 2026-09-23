"""run --record-new 测试：失败场景也生成运行记录。"""
from pathlib import Path

from typer.testing import CliRunner

from atk.cli import app
from atk.run_store import load_run

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]


def test_record_new_creates_entry(tmp_path: Path):
    # local 环境无服务，用例受阻——但记录必须生成（受阻退出码为 2）
    # 读侧（场景库/环境）用仓库绝对路径，写侧（记录、报告、--last 指针）全进 tmp：
    # 留在仓库里会写下指向不存在记录的僵尸指针，让 atk agent 对本工作区报假阻塞。
    repo_pointer = REPO / ".atk" / "last-run.json"
    before = repo_pointer.read_text(encoding="utf-8") if repo_pointer.exists() else None
    r = runner.invoke(app, [
        "run", "--env", "local", "--record-new",
        "--project", str(tmp_path),
        "--runs-dir", str(tmp_path / "runs"),
        "--report-dir", str(tmp_path / "reports"),
        "--root", str(REPO / "scenarios" / "demo"),
        "--env-file", str(REPO / "config" / "environments.yaml"),
    ])
    assert r.exit_code in (0, 1, 2), r.output
    entries = list((tmp_path / "runs").iterdir())
    assert len(entries) == 1
    rec = load_run(entries[0].name, str(tmp_path / "runs"))
    assert rec.scenarios, "记录应包含场景摘要"

    pointer = tmp_path / ".atk" / "last-run.json"
    assert pointer.is_file(), "--last 指针应落在 --project 指定的目录里"
    after = repo_pointer.read_text(encoding="utf-8") if repo_pointer.exists() else None
    assert after == before, "不该污染仓库工作区的 --last 指针"
