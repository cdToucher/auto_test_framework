"""编排链路：run_id 指针（--last）消除 AI 手工拼接。"""
import json

import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.last_run import (
    last_run_path,
    read_last_run,
    resolve_run_id,
    write_last_run,
)

cli = CliRunner()

SCENARIO = """
scenario: 登录成功
module: todo
priority: P0
tags: [smoke]
env: local
steps:
  - api:
      call: "GET /ping"
      expect: { status: 200 }
"""


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "a.yaml").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ------------------------------------------------------------------ 指针读写

def test_write_and_read_roundtrip(tmp_path):
    p = write_last_run(tmp_path, "smoke-1", base="main", head="HEAD", title="下单")
    assert p == last_run_path(tmp_path)
    data = read_last_run(tmp_path)
    assert data["run_id"] == "smoke-1"
    assert data["base"] == "main" and data["title"] == "下单"


def test_read_missing_returns_none(tmp_path):
    assert read_last_run(tmp_path) is None


def test_read_corrupted_returns_none(tmp_path):
    last_run_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    last_run_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert read_last_run(tmp_path) is None


def test_read_rejects_file_without_run_id(tmp_path):
    last_run_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    last_run_path(tmp_path).write_text('{"title": "x"}', encoding="utf-8")
    assert read_last_run(tmp_path) is None


def test_resolve_prefers_explicit_run_id(tmp_path):
    write_last_run(tmp_path, "from-pointer")
    assert resolve_run_id(tmp_path, "explicit", use_last=False) == "explicit"


def test_resolve_last_reads_pointer(tmp_path):
    write_last_run(tmp_path, "from-pointer")
    assert resolve_run_id(tmp_path, None, use_last=True) == "from-pointer"


def test_resolve_last_without_pointer_raises(tmp_path):
    with pytest.raises(LookupError, match="未找到最近运行记录"):
        resolve_run_id(tmp_path, None, use_last=True)


def test_resolve_nothing_raises(tmp_path):
    with pytest.raises(LookupError, match="缺少 run_id"):
        resolve_run_id(tmp_path, None, use_last=False)


# ------------------------------------------------------------------ CLI 集成

def test_plan_writes_pointer(proj):
    res = cli.invoke(app, ["plan", "--project", str(proj), "--runs-dir", str(proj / "reports" / "runs")])
    assert res.exit_code == 0, res.output
    data = read_last_run(proj)
    assert data is not None and data["run_id"].startswith("smoke-")


def test_last_command_reports_pointer(proj):
    write_last_run(proj, "abc-123", title="优惠券下单")
    res = cli.invoke(app, ["last", "--project", str(proj)])
    assert res.exit_code == 0
    assert "abc-123" in res.output and "优惠券下单" in res.output


def test_last_command_json(proj):
    write_last_run(proj, "abc-123", title="t")
    res = cli.invoke(app, ["last", "--project", str(proj), "--format", "json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["run_id"] == "abc-123"


def test_last_command_without_pointer_exits_2(tmp_path):
    res = cli.invoke(app, ["last", "--project", str(tmp_path)])
    assert res.exit_code == 2


def test_record_accepts_last(proj):
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    save_run(rec, runs)
    write_last_run(proj, rec.run_id)

    res = cli.invoke(
        app,
        ["record", "--last", "--title", "页面下单", "--status", "pass",
         "--project", str(proj), "--runs-dir", str(runs)],
    )
    assert res.exit_code == 0, res.output
    assert "页面下单" in res.output


def test_record_without_run_id_and_last_exits_2(proj):
    res = cli.invoke(app, ["record", "--title", "x", "--project", str(proj)])
    assert res.exit_code == 2


def test_record_json_output(proj):
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    save_run(rec, runs)
    write_last_run(proj, rec.run_id)

    res = cli.invoke(
        app,
        ["record", "--last", "--title", "页面下单", "--status", "pass",
         "--project", str(proj), "--runs-dir", str(runs), "--format", "json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["run_id"] == rec.run_id
    assert payload["status"] == "pass"
    assert payload["next"] == ["atk gate --last --format json"]


def test_gate_accepts_last(proj):
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    save_run(rec, runs)
    write_last_run(proj, rec.run_id)

    res = cli.invoke(
        app, ["gate", "--last", "--project", str(proj), "--runs-dir", str(runs),
              "--repo", str(proj)]
    )
    assert res.exit_code in (0, 1), res.output


def test_report_accepts_last(proj):
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    save_run(rec, runs)
    write_last_run(proj, rec.run_id)

    res = cli.invoke(
        app, ["report", "--last", "--project", str(proj), "--runs-dir", str(runs)]
    )
    assert res.exit_code == 0, res.output
    assert "report.html" in res.output


def test_review_accepts_last(proj):
    from atk.run_store import create_run, save_run

    runs = proj / "reports" / "runs"
    rec = create_run(runs_dir=runs)
    save_run(rec, runs)
    write_last_run(proj, rec.run_id)

    res = cli.invoke(
        app, ["review", "--last", "--by", "dev", "--verdict", "approve",
              "--project", str(proj), "--runs-dir", str(runs)]
    )
    assert res.exit_code == 0, res.output
    assert "已确认" in res.output


def test_smoke_json_manifest_is_pure_json_and_has_next(tmp_path, monkeypatch):
    """AI 只应凭 manifest 继续流程：输出必须是纯 JSON，且带 run_id 与 next。"""
    import subprocess

    repo = tmp_path
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "scenarios").mkdir()
    (repo / "scenarios" / "a.yaml").write_text(SCENARIO, encoding="utf-8")
    (repo / "config").mkdir()
    (repo / "config" / "environments.yaml").write_text(
        "local:\n  base_url: 'http://127.0.0.1:1'\n  vars: {}\n", encoding="utf-8"
    )
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    (repo / "src" / "x.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change"], check=True)

    monkeypatch.chdir(repo)
    res = cli.invoke(
        app,
        ["smoke", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
         "--env", "local", "--project", str(repo), "--format", "json"],
    )
    assert res.exit_code == 2, res.output  # 环境不可达
    payload = json.loads(res.output)  # 纯 JSON，可直接解析
    assert payload["run_id"].startswith("smoke-")
    assert payload["report"].endswith("report.html")
    assert isinstance(payload["next"], list) and payload["next"]
    assert payload["scenarios"]["total"] == 1

    # 指针已写入，后续命令可用 --last
    assert read_last_run(repo)["run_id"] == payload["run_id"]


def test_run_record_new_writes_pointer(proj):
    res = cli.invoke(
        app,
        ["run", "--env", "local", "--root", str(proj / "scenarios"),
         "--env-file", str(proj / "config" / "environments.yaml"),
         "--report-dir", str(proj / "reports"),
         "--runs-dir", str(proj / "reports" / "runs"),
         "--record-new", "--project", str(proj), "--format", "json"],
    )
    # 环境不可达 -> exit 2，但记录与指针应已写入
    assert res.exit_code == 2
    data = read_last_run(proj)
    assert data is not None and data["run_id"].startswith("smoke-")
    assert json.loads(res.output)["run_id"] == data["run_id"]
