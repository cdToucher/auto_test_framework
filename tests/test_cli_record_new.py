"""run --record-new 测试：失败场景也生成运行记录。"""
from pathlib import Path

from typer.testing import CliRunner

from atk.cli import app
from atk.run_store import load_run

runner = CliRunner()


def test_record_new_creates_entry(tmp_path: Path):
    # local 环境无服务，用例受阻——但记录必须生成（受阻退出码为 2）
    r = runner.invoke(app, [
        "run", "--env", "local", "--record-new",
        "--runs-dir", str(tmp_path / "runs"),
        "--root", "scenarios/demo",
    ])
    assert r.exit_code in (0, 1, 2)
    entries = list((tmp_path / "runs").iterdir())
    assert len(entries) == 1
    rec = load_run(entries[0].name, str(tmp_path / "runs"))
    assert rec.scenarios, "记录应包含场景摘要"
