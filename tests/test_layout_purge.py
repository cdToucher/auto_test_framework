"""单一 .atk 布局：init 收束、manifest 指纹、purge 安全清理、legacy 布局不变。"""
from pathlib import Path

import yaml
from typer.testing import CliRunner

from atk import layout
from atk.cli import app

runner = CliRunner()


def _init(tmp_path: Path, *extra: str) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    r = runner.invoke(app, ["init", "--root", str(proj), *extra])
    assert r.exit_code == 0, r.output
    return proj


def test_layout_detection(tmp_path):
    new = tmp_path / "new"
    new.mkdir()
    assert layout.mode(new) == "atk"
    legacy = tmp_path / "legacy"
    (legacy / "scenarios").mkdir(parents=True)
    assert layout.mode(legacy) == "legacy"
    assert layout.scenarios_dir(legacy) == legacy / "scenarios"
    assert layout.scenarios_dir(new) == new / ".atk" / "scenarios"


def test_init_converges_everything_under_atk(tmp_path):
    proj = _init(tmp_path, "--url", "http://s:1", "--modules", "order")
    # 项目根只允许三个根级文件：.atk/（产物收束）、AGENTS.md（AI 入口）、.gitignore
    top = {p.name for p in proj.iterdir()}
    assert top == {".atk", "AGENTS.md", ".gitignore"}, top
    gi = (proj / ".gitignore").read_text(encoding="utf-8")
    rules = [ln for ln in gi.splitlines() if ln and not ln.startswith("#")]
    assert ".atk/reports/" in rules
    assert not any("scenarios" in ln for ln in rules)  # 场景库必须照常入库
    atk = proj / ".atk"
    assert (atk / "environments.yaml").is_file()
    assert (atk / "modules.yaml").is_file()
    assert (atk / "scenarios" / "order" / "health.yaml").is_file()
    assert (atk / "skills" / "atk-smoke" / "SKILL.md").is_file()
    assert (atk / "reports" / "runs").is_dir()
    # manifest 记录创建文件与内容指纹
    man = json_load(atk / "manifest.json")
    assert "AGENTS.md" in man["created"]
    assert any(k.endswith("health.yaml") for k in man["created"])
    # 新布局下所有命令免参数可用
    import os

    cwd = os.getcwd()
    os.chdir(proj)
    try:
        assert runner.invoke(app, ["validate"]).exit_code == 0
        assert runner.invoke(app, ["run", "--dry-run"]).exit_code == 0
    finally:
        os.chdir(cwd)


def json_load(p: Path) -> dict:
    import json

    return json.loads(p.read_text(encoding="utf-8"))


def test_purge_roundtrip_safety(tmp_path):
    proj = _init(tmp_path, "--url", "http://s:1", "--modules", "order")
    atk = proj / ".atk"
    # 用户手工改动 environments；又新加了一个场景文件
    (atk / "environments.yaml").write_text("# 我改过\n", encoding="utf-8")
    user_scen = atk / "scenarios" / "order" / "my_case.yaml"
    user_scen.write_text("scenario: 我的\nsteps:\n  - api: {call: 'GET /x'}\n", encoding="utf-8")

    r = runner.invoke(app, ["purge", "--project", str(proj), "--yes"])
    assert r.exit_code == 0, r.output
    # 未改动的 init 物料全部消失
    assert not (atk / "modules.yaml").exists()
    assert not (atk / "scenarios" / "order" / "health.yaml").exists()
    assert not (atk / "skills").exists()
    assert not (proj / "AGENTS.md").exists()
    assert not (atk / "manifest.json").exists()
    # 改动过/用户自建的保留
    assert (atk / "environments.yaml").read_text(encoding="utf-8") == "# 我改过\n"
    assert user_scen.is_file()
    assert "保留" in r.output or "手工改动" in r.output


def test_purge_cleans_legacy_skill_copies(tmp_path):
    proj = _init(tmp_path)
    # 模拟旧版 init 铺下的散落拷贝
    legacy = proj / ".claude" / "skills" / "atk-smoke"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("atk 冒烟流程（旧拷贝）", encoding="utf-8")
    not_ours = proj / "skills" / "mine"
    not_ours.mkdir(parents=True)
    (not_ours / "SKILL.md").write_text("业务无关技能", encoding="utf-8")

    r = runner.invoke(app, ["purge", "--project", str(proj), "--yes"])
    assert r.exit_code == 0, r.output
    assert not (legacy / "SKILL.md").exists()
    assert (not_ours / "SKILL.md").is_file()  # 非 atk 命名空间不动


def test_init_legacy_project_keeps_old_paths(tmp_path):
    proj = tmp_path / "old"
    (proj / "scenarios").mkdir(parents=True)
    (proj / "config").mkdir()
    (proj / "config" / "environments.yaml").write_text("local: {base_url: 'http://x'}\n", encoding="utf-8")
    r = runner.invoke(app, ["init", "--root", str(proj), "--modules", "pay"])
    assert r.exit_code == 0, r.output
    assert (proj / "scenarios" / "pay" / "health.yaml").is_file()  # 仍写旧布局
    assert not (proj / ".atk" / "environments.yaml").exists()
    assert yaml.safe_load((proj / "config" / "environments.yaml").read_text())["local"]


def test_console_uses_new_layout(tmp_path):
    from fastapi.testclient import TestClient

    from atk.console import create_app

    proj = _init(tmp_path, "--modules", "order")
    client = TestClient(create_app(project_root=proj))
    tree = client.get("/api/tree").json()
    assert any(s["path"].endswith("order/health.yaml") for s in tree["scenarios"])
    assert "base_url" in client.get("/api/environments").json()["raw"]
    assert client.get("/api/schedules").json() == {"tasks": []}


def test_purge_dry_confirm_cancel(tmp_path):
    proj = _init(tmp_path)
    # 交互确认输入 n：取消后一切原样
    r = runner.invoke(app, ["purge", "--project", str(proj)], input="n\n")
    assert r.exit_code == 0, r.output
    assert (proj / ".atk" / "modules.yaml").is_file()
