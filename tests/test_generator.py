"""atk gen（AI 场景生成）测试：git 上下文包、YAML 校验落盘、LLM 客户端、CLI。"""
import json
import subprocess

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from atk.cli import app
from atk.generator.context import build_context, commit_log, file_patches, render_markdown
from atk.generator.llm import LlmError, resolve_config
from atk.generator.writer import extract_yaml_blocks, write_drafts

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


def _ctx(tmp_path, **kw):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    return build_context(
        "HEAD~1", "HEAD", str(tmp_path), mm, kw.get("root", tmp_path / "scenarios")
    )


# ---------- git 上下文包 ----------


def test_build_context_collects_commits_files_and_modules(repo, tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx["affected_modules"] == ["order"]
    assert [c["subject"] for c in ctx["commits"]] == ["feat: 新增下单接口"]
    assert "feat: 新增下单接口" in ctx["commits"][0]["body"] or \
        "支持按 skuId 创建订单" in ctx["commits"][0]["body"]
    assert ctx["files"] == ["src/api.py"]
    assert any(p["file"] == "src/api.py" and "+def create_order" in p["patch"]
               for p in ctx["patches"])


def test_commit_log_multiline_body_and_files(repo):
    cs = commit_log("HEAD~1", "HEAD", str(repo))
    assert cs[0]["short"] and cs[0]["files"] == ["src/api.py"]
    assert "支持按 skuId 创建订单" in cs[0]["body"]


def test_render_markdown_contains_all_sections(repo, tmp_path):
    md = render_markdown(_ctx(tmp_path))
    for fragment in ("## 提交记录", "feat: 新增下单接口", "src/api.py", "模块 `order`"):
        assert fragment in md


def test_file_patches_truncates(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _git("commit", "--allow-empty", "-qm", "init", cwd=tmp_path)
    (tmp_path / "big.py").write_text("\n".join(f"line{i} = {i}" for i in range(600)))
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "big", cwd=tmp_path)
    patches = file_patches("HEAD~1", "HEAD", str(tmp_path), max_lines_per_file=50)
    assert patches[0]["truncated"] and len(patches[0]["patch"].splitlines()) == 50


# ---------- YAML 提取与校验落盘 ----------


VALID_YAML = """
scenario: 按 skuId 创建订单后可查询
module: order
priority: P0
tags: [smoke]
steps:
  - api:
      call: "POST /api/orders"
      body: { skuId: "S1" }
      expect: { status: 200, data.orderNo: not_null }
"""


def test_extract_yaml_blocks_prefers_fenced():
    text = "说明文字\n```yaml\n" + VALID_YAML + "\n```\n尾巴"
    blocks = extract_yaml_blocks(text)
    assert len(blocks) == 1 and "scenario:" in blocks[0]


def test_extract_yaml_blocks_empty():
    assert extract_yaml_blocks("没有任何 yaml") == []


def _make_ctx(tmp_path, modules=("order",)):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": {"order": ["src/**"]}}), encoding="utf-8")
    return {"base": "HEAD~1", "head": "HEAD", "head_short": "abc1234",
            "commits": [{"short": "abc1234", "subject": "feat: x"}],
            "affected_modules": list(modules)}


def test_write_drafts_writes_validated_file_with_tag_and_header(tmp_path):
    ctx = _make_ctx(tmp_path)
    results = write_drafts([VALID_YAML], ctx, tmp_path / "scenarios")
    assert len(results) == 1 and results[0].ok
    path = tmp_path / "scenarios" / "order" / "gen-abc1234-1.yaml"
    assert path.exists() and results[0].path == str(path)
    text = path.read_text(encoding="utf-8")
    assert "ai-generated" in text and "AI 起草稿" in text and "abc1234" in text
    raw = yaml.safe_load(text)  # 头部注释会被 YAML 忽略
    assert "ai-generated" in raw["tags"]


def test_write_drafts_never_overwrites(tmp_path):
    ctx = _make_ctx(tmp_path)
    (tmp_path / "scenarios" / "order").mkdir(parents=True)
    taken = tmp_path / "scenarios" / "order" / "gen-abc1234-1.yaml"
    taken.write_text("scenario: 占位\nsteps: []\n", encoding="utf-8")
    results = write_drafts([VALID_YAML], ctx, tmp_path / "scenarios")
    assert results[0].ok and results[0].path.endswith("gen-abc1234-1-2.yaml")


def test_write_drafts_rejects_duplicate_name(tmp_path):
    ctx = _make_ctx(tmp_path)
    results = write_drafts(
        [VALID_YAML], ctx, tmp_path / "scenarios",
        existing_names={"按 skuId 创建订单后可查询"},
    )
    assert not results[0].ok and "重复" in results[0].reason


def test_write_drafts_rejects_module_outside_impact(tmp_path):
    ctx = _make_ctx(tmp_path, modules=("order",))
    bad = VALID_YAML.replace("module: order", "module: unknown")
    results = write_drafts([bad], ctx, tmp_path / "scenarios")
    assert not results[0].ok and "影响面" in results[0].reason


def test_write_drafts_rejects_invalid_yaml(tmp_path):
    ctx = _make_ctx(tmp_path)
    bad = "scenario: 只有名字没有步骤\nmodule: order\n"
    results = write_drafts([bad], ctx, tmp_path / "scenarios")
    assert not results[0].ok and "steps" in results[0].reason


def test_write_drafts_rejects_unknown_step_type(tmp_path):
    ctx = _make_ctx(tmp_path)
    bad = VALID_YAML.replace('call: "POST /api/orders"', "grpc: [1]")
    results = write_drafts([bad], ctx, tmp_path / "scenarios")
    assert not results[0].ok


# ---------- LLM 客户端 ----------


def test_resolve_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("ATK_LLM_API_KEY", raising=False)
    with pytest.raises(LlmError):
        resolve_config()


def test_resolve_config_env_fallback(monkeypatch):
    monkeypatch.setenv("ATK_LLM_API_KEY", "k-test")
    monkeypatch.setenv("ATK_LLM_MODEL", "my-model")
    cfg = resolve_config()
    assert cfg["api_key"] == "k-test" and cfg["model"] == "my-model"


def test_chat_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, payload=json)
        resp = httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": "```yaml\nscenario: x\n```"}}]},
        )
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)
    from atk.generator.llm import chat
    out = chat({"base_url": "https://x/v4", "api_key": "k", "model": "m"},
               [{"role": "user", "content": "hi"}])
    assert "scenario: x" in out
    assert captured["url"] == "https://x/v4/chat/completions"
    assert captured["payload"]["model"] == "m"


def test_chat_http_error_raises_llm_error(monkeypatch):
    from atk.generator.llm import chat
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **kw: httpx.Response(500, request=httpx.Request("POST", "http://x"),
                                        text="boom"),
    )
    with pytest.raises(LlmError, match="500"):
        chat({"base_url": "http://x", "api_key": "k", "model": "m"}, [])


# ---------- CLI ----------


def test_cli_gen_without_llm_prints_context(repo, tmp_path):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
    ])
    assert r.exit_code == 0
    assert "提交记录" in r.output and "feat: 新增下单接口" in r.output


def test_cli_gen_context_out(repo, tmp_path):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    out = tmp_path / "ctx.md"
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
        "--context-out", str(out),
    ])
    assert r.exit_code == 0 and out.exists() and "代码补丁" in out.read_text(encoding="utf-8")


def test_cli_gen_with_llm_writes_drafts(repo, tmp_path, monkeypatch):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    scenarios = tmp_path / "scenarios"

    def fake_chat(cfg, messages):
        assert "diff" in messages[1]["content"] or "src/api.py" in messages[1]["content"]
        return "好的\n```yaml\n" + VALID_YAML + "\n```\n"

    monkeypatch.setattr("atk.cli.llm_mod.chat", fake_chat)
    monkeypatch.setenv("ATK_LLM_API_KEY", "k-test")
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(scenarios), "--llm",
    ])
    assert r.exit_code == 0, r.output
    assert "已生成" in r.output
    drafts = list((scenarios / "order").glob("gen-*.yaml"))
    assert len(drafts) == 1
    raw = yaml.safe_load(drafts[0].read_text(encoding="utf-8"))
    assert raw["tags"] == ["smoke", "ai-generated"]


def test_cli_gen_llm_failure_exits_2(repo, tmp_path, monkeypatch):
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    from atk.generator.llm import LlmError
    monkeypatch.setattr(
        "atk.cli.llm_mod.chat",
        lambda *a, **kw: (_ for _ in ()).throw(LlmError("网络不通")),
    )
    monkeypatch.setenv("ATK_LLM_API_KEY", "k-test")
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"), "--llm",
    ])
    assert r.exit_code == 2 and "网络不通" in r.output


def test_cli_gen_missing_api_key_exits_2(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("ATK_LLM_API_KEY", raising=False)
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"), "--llm",
    ])
    assert r.exit_code == 2 and "API Key" in r.output


def test_cli_gen_no_changes(repo, tmp_path):
    _git("commit", "--allow-empty", "-qm", "e", cwd=repo)
    mm = tmp_path / "modules.yaml"
    mm.write_text(yaml.safe_dump({"modules": MODULE_MAP}), encoding="utf-8")
    r = runner.invoke(app, [
        "gen", "--base", "HEAD~1", "--head", "HEAD", "--repo", str(repo),
        "--module-map", str(mm), "--root", str(tmp_path / "scenarios"),
    ])
    assert r.exit_code == 0 and "无变更" in r.output
