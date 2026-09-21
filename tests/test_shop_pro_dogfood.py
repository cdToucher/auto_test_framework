"""shop_pro 复杂演示的端到端 dogfood：smoke 建单 -> ui_pending -> record 回填 -> gate。

同时覆盖 init 一键配置与 run --set 临时变量两条新起步路径。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atk.cli import app
from examples.shop_pro import make_server

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def shop_base_url():
    srv = make_server(0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def shop_project(tmp_path, shop_base_url):
    """把 scenarios/shop_pro 装进一个带 git 基线的独立工程（模拟真实项目接入）。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "environments.yaml").write_text(
        f"spapro:\n  base_url: {shop_base_url}\n"
        "  vars:\n    username: qa\n    password: secret123\n",
        encoding="utf-8",
    )
    (cfg / "modules.yaml").write_text(
        "modules:\n"
        "  auth:\n    - \"src/auth/**\"\n"
        "  shop:\n    - \"src/shop/**\"\n"
        "  order:\n    - \"src/order/**\"\n",
        encoding="utf-8",
    )
    shutil.copytree(REPO_ROOT / "scenarios" / "shop_pro", tmp_path / "scenarios")
    (tmp_path / "fixtures").mkdir()
    shutil.copy(REPO_ROOT / "fixtures" / "shop_pro.yaml", tmp_path / "fixtures")
    _git("init", "-q", cwd=tmp_path)
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "接入 atk", cwd=tmp_path)
    for m in ("auth", "shop", "order"):
        d = tmp_path / "src" / m
        d.mkdir(parents=True)
        (d / "main.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "业务改动", cwd=tmp_path)
    return tmp_path


def test_full_smoke_record_gate(shop_project, tmp_path, monkeypatch):
    monkeypatch.chdir(shop_project)

    # 1) 一键冒烟：API 场景应全过；UI 场景登记为待实测意图 -> gate 拦截
    sm = runner.invoke(
        app, ["smoke", "--base", "HEAD~1", "--head", "HEAD",
              "--title", "券商城", "--format", "json"]
    )
    assert sm.exit_code == 1, sm.output  # gate 因 pending 拦截
    manifest = json.loads(sm.output)
    assert manifest["scenarios"]["failed"] == 0
    assert manifest["scenarios"]["total"] >= 11
    assert manifest["intents_pending"] == ["页面完成加购用券下单并显示待支付"]
    assert manifest["gate"] is False and manifest["full_run"] is False
    assert any("--status <pass|fail|suspect|blocked>" in n for n in manifest["next"])

    # 2) AI 实测后回填（同名 pending 意图就地更新）
    shot = tmp_path / "ui.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    result = tmp_path / "ui-result.json"
    result.write_text(
        json.dumps(
            {
                "title": "页面完成加购用券下单并显示待支付",
                "status": "pass",
                "note": "页面弹单号 SP-000x，订单列表 pending、应付 50 元",
                "evidence": [str(shot)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rc = runner.invoke(app, ["record", "--last", "--from-json", str(result)])
    assert rc.exit_code == 0, rc.output

    # 3) 回填后 gate 放行（未经 review 仅告警）
    g = runner.invoke(app, ["gate", "--last", "--format", "json"])
    assert g.exit_code == 0, g.output
    gj = json.loads(g.output)
    assert gj["passed"] is True and gj["blocking"] == []
    assert any("未经开发确认" in w for w in gj["warnings"])

    # 4) 整单确认后仍放行，且报告含证据
    rv = runner.invoke(app, ["review", "--last", "--by", "dev1", "--verdict", "approve"])
    assert rv.exit_code == 0, rv.output
    g2 = runner.invoke(app, ["gate", "--last"])
    assert g2.exit_code == 0, g2.output
    assert "已获开发确认" in g2.output
    rp = runner.invoke(app, ["report", "--last"])
    assert rp.exit_code == 0, rp.output
    html = (shop_project / "reports" / "runs" / manifest["run_id"] / "report.html").read_text(
        encoding="utf-8"
    )
    assert "evidence/ui.png" in html
    assert "页面完成加购用券下单并显示待支付" in html


def test_run_dry_then_empty_selection(shop_project, monkeypatch):
    monkeypatch.chdir(shop_project)
    d = runner.invoke(app, ["run", "--dry-run", "--module", "order", "--format", "json"])
    assert d.exit_code == 0, d.output
    names = [s["name"] for s in json.loads(d.output)["selected"]]
    assert len(names) == 7  # order 目录 6 个 API + 1 个 UI 场景
    assert "页面完成加购用券下单并显示待支付" in names
    e = runner.invoke(app, ["run", "--module", "nosuch"])
    assert e.exit_code == 2, e.output


# ---------- init 一键起步 ----------

def test_init_quickstart_with_url_and_modules(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    r = runner.invoke(
        app,
        ["init", "--root", str(proj), "--url", "http://127.0.0.1:9999",
         "--env-name", "staging", "--modules", "order,coupon"],
    )
    assert r.exit_code == 0, r.output
    env_text = (proj / ".atk" / "environments.yaml").read_text(encoding="utf-8")
    assert "staging:" in env_text and "http://127.0.0.1:9999" in env_text
    mods_text = (proj / ".atk" / "modules.yaml").read_text(encoding="utf-8")
    assert "order:" in mods_text and "coupon:" in mods_text
    assert (proj / ".atk" / "scenarios" / "order" / "health.yaml").is_file()
    assert (proj / ".atk" / "scenarios" / "coupon" / "health.yaml").is_file()
    # 幂等：第二次全部跳过
    r2 = runner.invoke(app, ["init", "--root", str(proj)])
    assert r2.exit_code == 0
    assert "跳过" in r2.output
    # 生成的场景可直接通过校验
    import os

    cwd = os.getcwd()
    os.chdir(proj)
    try:
        v = runner.invoke(app, ["validate"])
    finally:
        os.chdir(cwd)
    assert v.exit_code == 0, v.output
    assert "0 个文件错误" in v.output


def test_init_rejects_bad_module_name(tmp_path):
    proj = tmp_path / "p2"
    proj.mkdir()
    r = runner.invoke(app, ["init", "--root", str(proj), "--modules", "订单A"])
    assert r.exit_code != 0
    assert "小写字母开头" in r.output


def test_init_vars_are_optional_and_injectable(tmp_path):
    import yaml

    # 1) 默认模板：vars 留空 + 注释示例，不再有写死的 testuser/testpass
    plain = tmp_path / "plain"
    plain.mkdir()
    assert runner.invoke(app, ["init", "--root", str(plain)]).exit_code == 0
    text = (plain / ".atk" / "environments.yaml").read_text(encoding="utf-8")
    assert "vars: {}" in text
    assert "\n    username:" not in text and "\n    password:" not in text  # 仅注释示例
    assert yaml.safe_load(text)["local"]["vars"] == {}

    # 2) --var 注入 token/cookie 型凭据（${env:} 占位符原样落盘且可解析）
    proj = tmp_path / "tok"
    proj.mkdir()
    r = runner.invoke(
        app,
        ["init", "--root", str(proj), "--url", "http://api.internal:8080",
         "--env-name", "staging",
         "--var", "token=${env:ATK_TOKEN}",
         "--var", "cookie=${env:ATK_COOKIE}"],
    )
    assert r.exit_code == 0, r.output
    text = (proj / ".atk" / "environments.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)["staging"]
    assert cfg["base_url"] == "http://api.internal:8080"
    assert cfg["vars"] == {"token": "${env:ATK_TOKEN}", "cookie": "${env:ATK_COOKIE}"}

    # 3) --var 未配 --url / 非法键：显式报错
    bad = runner.invoke(app, ["init", "--root", str(tmp_path / "b1"), "--var", "t=1"])
    assert bad.exit_code != 0 and "--url" in bad.output
    bad2 = runner.invoke(
        app, ["init", "--root", str(tmp_path / "b2"), "--url", "http://x",
              "--var", "a-b=1"]
    )
    assert bad2.exit_code != 0 and "标识符" in bad2.output


# ---------- run --set 临时变量 ----------

def test_set_vars_override(tmp_path, mock_base_url, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "environments.yaml").write_text(
        f"local:\n  base_url: {mock_base_url}\n", encoding="utf-8"
    )
    sc = tmp_path / "scenarios"
    sc.mkdir()
    (sc / "probe.yaml").write_text(
        'scenario: 路径探针\nmodule: m\nenv: local\nsteps:\n'
        '  - api:\n      call: "GET /${probe}"\n      expect: {status: 200}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    miss = runner.invoke(app, ["run"])
    assert miss.exit_code == 1, miss.output  # 未 --set 时按配置错误显性失败
    hit = runner.invoke(app, ["run", "--set", "probe=ping"])
    assert hit.exit_code == 0, hit.output
    assert "通过 1/1" in hit.output
