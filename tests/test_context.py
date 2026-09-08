"""atk context（确定性变更上下文包）测试。"""
import json
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app

runner = CliRunner()


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """两笔提交的迷你仓库：第一笔空提交，第二笔新增 src/api.py。"""
    _git("init", "-q", cwd=tmp_path)
    _git("commit", "--allow-empty", "-qm", "init", cwd=tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "api.py").write_text("def create_order():\n    return {'orderNo': 'A1'}\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "feat: 新增下单接口\n\n支持按 skuId 创建订单", cwd=tmp_path)
    return tmp_path


MODULE_MAP = {"order": ["src/**"]}


def _module_map(tmp_path):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    return mm


def test_context_prints_all_sections(repo, tmp_path):
    mm = _module_map(tmp_path)
    r = runner.invoke(app, [
        "context", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
    ])
    assert r.exit_code == 0, r.output
    assert "## 提交记录" in r.output
    assert "## 变更文件与模块归属" in r.output
    assert "## 代码补丁" in r.output
    assert "feat: 新增下单接口" in r.output
    assert "src/api.py" in r.output
    assert "模块 `order`" in r.output


def test_context_out_writes_file(repo, tmp_path):
    mm = _module_map(tmp_path)
    out = tmp_path / "ctx.md"
    r = runner.invoke(app, [
        "context", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
        "--context-out", str(out),
    ])
    assert r.exit_code == 0, r.output
    assert out.exists()
    assert "## 代码补丁" in out.read_text(encoding="utf-8")


def test_context_help_has_no_llm():
    r = runner.invoke(app, ["context", "--help"])
    assert r.exit_code == 0, r.output
    assert "--llm" not in r.output


def test_context_no_changes(repo, tmp_path):
    _git("commit", "--allow-empty", "-qm", "e", cwd=repo)
    mm = _module_map(tmp_path)
    r = runner.invoke(app, [
        "context", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
    ])
    assert r.exit_code == 0, r.output
    assert "无需生成" in r.output or "无变更" in r.output


def test_context_json_format(repo, tmp_path):
    mm = _module_map(tmp_path)
    r = runner.invoke(app, [
        "context", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
        "--format", "json",
    ])
    assert r.exit_code == 0, r.output
    # json 直出 stdout 时必须纯 JSON 可解析（下一步提示已省略，保证机器解析）
    data = json.loads(r.output)
    assert data["files"] == ["src/api.py"]
    assert data["affected_modules"] == ["order"]
    assert "下一步" not in r.stdout


def test_context_invalid_format_exits_2(repo, tmp_path):
    mm = _module_map(tmp_path)
    r = runner.invoke(app, [
        "context", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
        "--format", "xml",
    ])
    assert r.exit_code == 2


def test_context_git_failure_exits_2(repo, tmp_path):
    mm = _module_map(tmp_path)
    r = runner.invoke(app, [
        "context", "--base", "NOTEXIST", "--head", "ALSONOTEXIST", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
    ])
    assert r.exit_code == 2
