"""atk context 上下文包测试：git 上下文包、补丁截断、markdown 渲染。"""
import subprocess

import pytest
import yaml

from atk.generator.context import build_context, commit_log, file_patches, render_markdown


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
    assert ctx["commits"][0]["body"] and \
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
