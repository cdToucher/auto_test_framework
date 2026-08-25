from pathlib import Path

from typer.testing import CliRunner

from atk.cli import app

SCENARIO = (
    "scenario: 冒烟登录\nmodule: user\npriority: P0\ntags: [smoke]\nenv: it\n"
    "steps:\n"
    "  - api:\n"
    '      call: "POST /api/login"\n'
    "      body: {username: alice, password: secret123}\n"
    "      expect: {status: 200, data.token: not_null}\n"
)


def _setup(tmp_path: Path, base_url: str):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/environments.yaml").write_text(
        f"it:\n  base_url: {base_url}\n  vars: {{}}\n", encoding="utf-8"
    )
    (tmp_path / "scenarios/user").mkdir(parents=True)
    (tmp_path / "scenarios/user/login.yaml").write_text(SCENARIO, encoding="utf-8")


def test_list_command(tmp_path, mock_base_url, monkeypatch):
    _setup(tmp_path, mock_base_url)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["list"])
    assert r.exit_code == 0
    assert "冒烟登录" in r.output and "P0" in r.output


def test_run_command_writes_report(tmp_path, mock_base_url, monkeypatch):
    _setup(tmp_path, mock_base_url)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["run", "--env", "it"])
    assert r.exit_code == 0, r.output
    assert "通过 1/1" in r.output or "通过 1" in r.output
    reports = list((tmp_path / "reports").glob("*.html"))
    assert reports, "应生成报告文件"


def test_run_without_env_uses_scenario_env(tmp_path, mock_base_url, monkeypatch):
    _setup(tmp_path, mock_base_url)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["run"])
    assert r.exit_code == 0, r.output


def test_run_unknown_env_exits_2_with_message(tmp_path, mock_base_url, monkeypatch):
    _setup(tmp_path, mock_base_url)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["run", "--env", "nope"])
    assert r.exit_code == 2
    assert "环境配置错误" in r.output
